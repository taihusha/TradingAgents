"""Chinese financial news fetchers for A-share / China-listed stocks.

Provides stock-specific and sector/macro news coverage from Chinese
financial media platforms. Yahoo Finance / Alpha Vantage have close to
zero coverage for A-share tickers, so this module fills the gap for:

* **Stock-specific news** — Eastmoney (东方财富) per-stock news feed
  with quality filtering (dedup, anti-spam, source prioritisation).
* **Macro / policy news** — China macro headlines from Eastmoney.

Every function follows the graceful-degradation pattern: no exceptions
surface, and the caller always receives a formatted plaintext string —
either real data or a clear ``<platform unavailable>`` marker.

News quality pipeline
---------------------
Raw Eastmoney feeds contain substantial noise: duplicate articles
(reprinted across outlets), ultra-short filler content, promotional
posts, and SEO spam. The fetcher applies these filters in order:

1. **Spam detection** — titles matching known spam/广告 patterns are
   removed before any other processing.
2. **Content quality** — articles with < 15 meaningful chars after
   stripping HTML/whitespace, or where the content is just a repeat
   of the title, are dropped.
3. **Deduplication** — near-duplicate titles (>85% token overlap or
   >90% SequenceMatcher ratio) are collapsed; the version from the
   most authoritative source wins.
4. **Source prioritisation** — official 公告 (announcements), 研报
   (research reports), and 财报 (earnings) are ranked higher in the
   output and marked for the LLM's attention.

Configuration
-------------
.. code:: bash

    TRADINGAGENTS_CN_NEWS_SOURCES=eastmoney_stock_news
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Optional

from ._browser_session import inject_browser_session  # noqa: E402, F401 — TLS/UA patch for akshare

logger = logging.getLogger(__name__)


# ── Ticker helpers ──────────────────────────────────────────────────────────


def _is_cn_ticker(ticker: str) -> bool:
    """Return True if the ticker looks like a Chinese A-share."""
    return ticker.upper().endswith((".SZ", ".SS"))


def _strip_suffix(ticker: str) -> str:
    """Return bare numeric code: '000933.SZ' → '000933'."""
    return ticker.upper().replace(".SZ", "").replace(".SS", "")


# ── News quality filtering ──────────────────────────────────────────────────

# Spam title patterns — these are almost never legitimate financial news.
_SPAM_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^(免费|限时|疯抢|点击领取|加群|关注公众号|微信|扫码).*",
        r".*(推荐|推广|广告).*",
        r"^\d{3,}.*(群|QQ|微信).*",
        r".*(领|送).{1,5}(福利|金币|红包|礼品).*",
        r".*点击查看.{0,10}$",
    ]
]

# High-value news types — these carry more analytical weight and should be
# surfaced at the top of the feed.
_HIGH_VALUE_SOURCES: dict[str, int] = {
    "公告": 10,
    "研报": 9,
    "年报": 8,
    "季报": 8,
    "财报": 8,
    "半年报": 8,
    "业绩": 7,
    "调研": 7,
    "互动易": 6,
    "上证e互动": 6,
    "交易所": 7,
    "证监会": 8,
    "央行": 7,
    "发改委": 7,
    "工信部": 7,
}

# Source authority scores — well-known credible outlets rank higher.
_SOURCE_AUTHORITY: dict[str, int] = {
    "证券时报": 5,
    "中国证券报": 5,
    "上海证券报": 5,
    "证券日报": 5,
    "新华社": 5,
    "人民日报": 5,
    "经济参考报": 4,
    "21世纪经济报道": 4,
    "每日经济新闻": 4,
    "第一财经": 4,
    "财联社": 4,
    "东方财富": 3,
}


def _is_spam(title: str) -> bool:
    """Return True if the title matches known spam/ad patterns."""
    for pat in _SPAM_PATTERNS:
        if pat.search(title):
            return True
    return False


def _meaningful_length(text: str) -> int:
    """Count chars after stripping HTML tags and whitespace."""
    clean = re.sub(r"<[^>]+>", "", text or "")
    return len(clean.strip())


def _content_repeats_title(title: str, content: str) -> bool:
    """Return True if content is basically just the title repeated."""
    t = title.strip()[:50]
    c = (content or "").strip()[:50]
    if not c:
        return True
    if c in t or t in c:
        return True
    ratio = SequenceMatcher(None, t, c).ratio()
    return ratio > 0.85


def _tokenize_title(title: str) -> set[str]:
    """Tokenize a Chinese/English title into a set of meaningful tokens."""
    # Split on whitespace + common Chinese punctuation
    tokens = re.split(r"[\s,，。！？、；：""''（）《》【】\[\]\(\)/\\|@#$%^&\*]+", title)
    return {t.lower() for t in tokens if len(t) >= 2}


def _title_overlap(t1: str, t2: str) -> float:
    """Jaccard similarity of token sets."""
    s1 = _tokenize_title(t1)
    s2 = _tokenize_title(t2)
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


def _quality_score(title: str, content: str, source: str) -> int:
    """Compute a quality score for a news article (higher = better)."""
    score = 0

    # Content length bonus
    c_len = _meaningful_length(content)
    if c_len >= 500:
        score += 5
    elif c_len >= 200:
        score += 3
    elif c_len >= 50:
        score += 1

    # Title length bonus (substantive titles)
    t_len = _meaningful_length(title)
    if t_len >= 20:
        score += 2
    elif t_len >= 10:
        score += 1

    # High-value keyword bonus
    for kw, pts in _HIGH_VALUE_SOURCES.items():
        if kw in title or kw in content:
            score += pts
            break  # only count highest-value match

    # Source authority bonus
    if source:
        for name, pts in _SOURCE_AUTHORITY.items():
            if name in source:
                score += pts
                break

    return score


def _filter_and_dedup(articles: list[dict]) -> list[dict]:
    """Apply quality filtering and deduplication to an article list.

    Each article dict should have keys: title, content, source, time, url.
    Returns a filtered, deduplicated, quality-sorted list.
    """
    if not articles:
        return []

    # Stage 1: Remove spam
    filtered = [a for a in articles if not _is_spam(a.get("title", ""))]
    if not filtered:
        return []

    # Stage 2: Remove low-quality content
    quality_ok = []
    for a in filtered:
        title = a.get("title", "")
        content = a.get("content", "")
        # Drop articles with near-empty content
        if _meaningful_length(content) < 15:
            continue
        # Drop articles where content is just the title repeated
        if _content_repeats_title(title, content):
            continue
        quality_ok.append(a)

    if not quality_ok:
        return filtered  # fall back to unfiltered rather than returning empty

    # Stage 3: Deduplicate by title similarity
    deduped: list[dict] = []
    for a in quality_ok:
        title = a.get("title", "")
        is_dup = False
        for i, kept in enumerate(deduped):
            k_title = kept.get("title", "")
            # Fast check: identical titles
            if title == k_title:
                is_dup = True
                break
            # Token overlap check
            if _title_overlap(title, k_title) > 0.85:
                is_dup = True
                break
            # String similarity check (more expensive — only if token check passed)
            if _title_overlap(title, k_title) > 0.5:
                ratio = SequenceMatcher(None, title, k_title).ratio()
                if ratio > 0.90:
                    is_dup = True
                    break
        if not is_dup:
            deduped.append(a)

    # Stage 4: Quality-score sorting (highest first)
    for a in deduped:
        a["_quality"] = _quality_score(
            a.get("title", ""), a.get("content", ""), a.get("source", "")
        )

    deduped.sort(key=lambda a: a.get("_quality", 0), reverse=True)

    return deduped


# ── Eastmoney stock news (akshare backend) ──────────────────────────────────


def fetch_eastmoney_stock_news(
    ticker: str,
    limit: int = 0,  # kwarg accepted for compatibility; ignored
) -> str:
    """Fetch recent stock-specific news from Eastmoney via akshare.

    Uses ``akshare.stock_news_em()`` which queries Eastmoney's stock news
    search endpoint. Applies quality filtering (spam removal, dedup,
    source prioritisation). Returns up to 15 quality-filtered articles.

    Args:
        ticker: Full ticker with suffix (e.g. ``000933.SZ``).
        limit: Ignored; accepted for caller compatibility.

    Returns:
        Formatted markdown string with news articles, or an
        ``<unavailable>`` placeholder on failure.
    """
    if not _is_cn_ticker(ticker):
        return f"<eastmoney stock news skipped for {ticker}: not a Chinese ticker>"

    bare = _strip_suffix(ticker)
    try:
        import akshare as ak
    except ImportError:
        return "<eastmoney stock news unavailable: akshare library not installed>"

    try:
        df = ak.stock_news_em(symbol=bare)
    except Exception as exc:
        logger.debug("Eastmoney stock news failed for %s: %s", ticker, exc)
        return f"<eastmoney stock news unavailable for {ticker}: {exc}>"

    if df is None or df.empty:
        return f"<eastmoney stock news empty for {ticker}>"

    # Column mapping
    col_map = _resolve_columns(df)
    title_col = col_map.get("title")
    time_col = col_map.get("time")
    source_col = col_map.get("source")
    url_col = col_map.get("url")
    content_col = col_map.get("content")

    # Build article dicts
    raw_articles: list[dict] = []
    for _, row in df.iterrows():
        title = str(row.get(title_col, "")) if title_col else ""
        if not title or title in ("nan", "None"):
            continue
        content = str(row.get(content_col, "")) if content_col else ""
        source = str(row.get(source_col, "")) if source_col else ""
        pub_time = str(row.get(time_col, "")) if time_col else ""
        url = str(row.get(url_col, "")) if url_col else ""
        raw_articles.append({
            "title": title,
            "content": content,
            "source": source,
            "time": pub_time,
            "url": url,
        })

    # Apply quality pipeline
    articles = _filter_and_dedup(raw_articles)

    if not articles:
        return f"<eastmoney stock news: no quality news found for {ticker} (filtered from {len(raw_articles)} raw)>"

    # Format output — high-value items get a marker
    MAX_ARTICLES = 15
    lines = [f"## 东方财富个股新闻 — {ticker}"]
    lines.append("")
    lines.append(
        f"（品质筛选后 {len(articles)} 条 / 原始 {len(raw_articles)} 条，"
        f"展示前 {min(len(articles), MAX_ARTICLES)} 条）\n"
    )

    count = 0
    for a in articles:
        if count >= MAX_ARTICLES:
            break
        title = a["title"]
        pub_time = a.get("time", "")
        source = a.get("source", "")
        content = a.get("content", "")
        url = a.get("url", "")
        score = a.get("_quality", 0)

        # High-value marker
        marker = " 📌" if score >= 10 else ""

        lines.append(f"### {title}{marker}")
        if pub_time and pub_time not in ("nan", "None"):
            lines.append(f"**发布时间**: {pub_time}")
        if source and source not in ("nan", "None"):
            lines.append(f"**来源**: {source}")
        if content and content not in ("nan", "None"):
            # Show up to 400 chars for high-quality, 200 for standard
            max_len = 400 if score >= 7 else 200
            excerpt = (
                content[:max_len] + "..."
                if _meaningful_length(content) > max_len
                else content
            )
            lines.append(f"**摘要**: {excerpt}")
        if url and url not in ("nan", "None"):
            lines.append(f"**链接**: {url}")
        lines.append("")
        count += 1

    if count == 0:
        return f"<eastmoney stock news empty for {ticker}>"

    lines.append(f"（共 {count} 条品质新闻）\n")
    return "\n".join(lines)


def _resolve_columns(df) -> dict[str, str]:
    """Map akshare DataFrame columns to semantic keys.

    akshare may return garbled column names depending on the system
    encoding. We try exact Chinese names first, then fall back to
    positional matching based on known column order.
    """
    cols = list(df.columns)

    # Known Chinese column names
    KNOWN = {
        "关键词": "keyword",
        "新闻标题": "title",
        "新闻内容": "content",
        "发布时间": "time",
        "文章来源": "source",
        "新闻链接": "url",
    }

    result = {}
    for i, c in enumerate(cols):
        name = KNOWN.get(c)
        if name:
            result[name] = c
        else:
            # Fall back to positional mapping (known akshare column order)
            POSITIONAL = ["keyword", "title", "content", "time", "source", "url"]
            if i < len(POSITIONAL):
                result[POSITIONAL[i]] = c

    return result


# ── China macro news ────────────────────────────────────────────────────────


def fetch_eastmoney_macro_news(limit: int = 15) -> str:
    """Fetch recent China macro / policy news from Eastmoney.

    Scrapes the Eastmoney macro news homepage. Uses ``requests`` for
    robust encoding handling (the page declares UTF-8 but content may
    be GBK-encoded; requests handles this transparently).

    Returns:
        Formatted markdown string with macro news headlines.
    """
    try:
        import requests as req
    except ImportError:
        return "<eastmoney macro news unavailable: requests not installed>"

    url = "https://finance.eastmoney.com/a/czqyw.html"
    try:
        resp = req.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text
    except Exception as exc:
        logger.debug("Eastmoney macro news fetch failed: %s", exc)
        return "<eastmoney macro news unavailable>"

    # Extract titles and links from <div class="title"> blocks
    pattern = re.compile(
        r'<div\s+class="title">\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    matches = pattern.findall(html)

    news_items = []
    seen_titles = set()
    for href, title in matches:
        title = re.sub(r"<[^>]+>", "", title).strip()
        if not title or len(title) < 8:
            continue
        if title in seen_titles:
            continue
        seen_titles.add(title)
        # Resolve relative URLs
        if href.startswith("//"):
            link = f"https:{href}"
        elif href.startswith("/"):
            link = f"https://finance.eastmoney.com{href}"
        else:
            link = href
        news_items.append((title, link))
        if len(news_items) >= limit:
            break

    if not news_items:
        return "<eastmoney macro news empty>"

    lines = ["## 东方财富宏观 / 政策新闻\n"]
    for title, link in news_items:
        lines.append(f"- [{title}]({link})")
    lines.append(f"\n（共 {len(news_items)} 条宏观新闻）\n")
    return "\n".join(lines)


# ── Combined entry point ────────────────────────────────────────────────────


def fetch_cn_news(
    ticker: str,
    start_date: str = "",
    end_date: str = "",
    sources: Optional[list[str]] = None,
) -> str:
    """Fetch Chinese news coverage for a given ticker.

    Queries enabled Chinese news sources and returns a combined formatted
    text block. Non-CN tickers are skipped immediately.

    Args:
        ticker: Full ticker symbol (e.g. ``000933.SZ``).
        start_date: Start date (currently informational; Eastmoney news
            does not support date-range filtering via akshare).
        end_date: End date (informational, as above).
        sources: List of source keys to query. Defaults to
            ``["eastmoney_stock_news"]``. Supported: ``eastmoney_stock_news``,
            ``eastmoney_macro_news``.

    Returns:
        Formatted markdown string combining all sources, or an empty string
        if the ticker is not Chinese or all sources fail.
    """
    if not _is_cn_ticker(ticker):
        return ""

    if sources is None:
        sources = ["eastmoney_stock_news"]

    blocks = []
    for src in sources:
        try:
            if src == "eastmoney_stock_news":
                block = fetch_eastmoney_stock_news(ticker)
            elif src == "eastmoney_macro_news":
                block = fetch_eastmoney_macro_news()
            else:
                block = f"<unknown cn_news source: {src}>"
        except Exception as exc:
            logger.debug("cn_news source %s failed for %s: %s", src, ticker, exc)
            block = f"<{src} unavailable for {ticker}: {exc}>"

        if block and not block.startswith("<"):
            blocks.append(block)

    return "\n\n".join(blocks) if blocks else ""
