"""Chinese financial news fetchers for A-share / China-listed stocks.

Provides stock-specific and sector/macro news coverage from Chinese
financial media platforms. Yahoo Finance / Alpha Vantage have close to
zero coverage for A-share tickers, so this module fills the gap for:

* **Stock-specific news** — Eastmoney (东方财富) per-stock news feed
* **Macro / policy news** — China macro queries via Eastmoney

Every function follows the graceful-degradation pattern: no exceptions
surface, and the caller always receives a formatted plaintext string —
either real data or a clear ``<platform unavailable>`` marker.

Auto-detection
--------------
Chinese A-share tickers use numeric codes with ``.SZ`` (Shenzhen) or
``.SS`` (Shanghai) suffixes. Non-CN tickers (e.g. ``AAPL``) return
a placeholder immediately.

Configuration
-------------
Opt-in via the ``cn_news_sources`` config key (list). Defaults to
``["eastmoney_stock_news"]`` when unset for Chinese tickers — A-share
analysis needs this to produce meaningful catalyst/risk signals.

.. code:: bash

    TRADINGAGENTS_CN_NEWS_SOURCES=eastmoney_stock_news
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ── Ticker helpers ────────────────────────────────────────────────────────


def _is_cn_ticker(ticker: str) -> bool:
    """Return True if the ticker looks like a Chinese A-share."""
    return ticker.upper().endswith((".SZ", ".SS"))


def _strip_suffix(ticker: str) -> str:
    """Return bare numeric code: '000933.SZ' → '000933'."""
    return ticker.upper().replace(".SZ", "").replace(".SS", "")


# ── Eastmoney stock news (akshare backend) ────────────────────────────────


def fetch_eastmoney_stock_news(
    ticker: str,
    limit: int = 0,  # kwarg accepted for compatibility; ignored
) -> str:
    """Fetch recent stock-specific news from Eastmoney via akshare.

    Uses ``akshare.stock_news_em()`` which queries Eastmoney's stock news
    search endpoint. Returns up to 10 recent news articles for the given
    stock code.

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
        return (
            "<eastmoney stock news unavailable: akshare library not installed>"
        )

    try:
        df = ak.stock_news_em(symbol=bare)
    except Exception as exc:
        logger.debug("Eastmoney stock news failed for %s: %s", ticker, exc)
        return f"<eastmoney stock news unavailable for {ticker}: {exc}>"

    if df is None or df.empty:
        return f"<eastmoney stock news empty for {ticker}>"

    # Column mapping (akshare returns columns in Chinese)
    col_map = _resolve_columns(df)
    title_col = col_map.get("title")
    time_col = col_map.get("time")
    source_col = col_map.get("source")
    url_col = col_map.get("url")
    content_col = col_map.get("content")

    lines = [f"## 东方财富个股新闻 — {ticker}\n"]
    count = 0
    for _, row in df.iterrows():
        title = str(row.get(title_col, "")) if title_col else ""
        pub_time = str(row.get(time_col, "")) if time_col else ""
        source = str(row.get(source_col, "")) if source_col else ""
        url = str(row.get(url_col, "")) if url_col else ""
        content = str(row.get(content_col, "")) if content_col else ""

        if not title or title in ("nan", "None"):
            continue

        lines.append(f"### {title}")
        if pub_time and pub_time not in ("nan", "None"):
            lines.append(f"**发布时间**: {pub_time}")
        if source and source not in ("nan", "None"):
            lines.append(f"**来源**: {source}")
        if content and content not in ("nan", "None") and len(content) > 5:
            # Truncate long content to avoid overwhelming the prompt
            excerpt = content[:300] + "..." if len(content) > 300 else content
            lines.append(f"**摘要**: {excerpt}")
        if url and url not in ("nan", "None"):
            lines.append(f"**链接**: {url}")
        lines.append("")
        count += 1

    if count == 0:
        return f"<eastmoney stock news empty for {ticker}>"

    lines.append(f"（共 {count} 条新闻）\n")
    return "\n".join(lines)


def _resolve_columns(df) -> dict[str, str]:
    """Map akshare DataFrame columns to semantic keys.

    akshare may return garbled column names depending on the system
    encoding. We try exact Chinese names first, then fall back to
    positional matching based on known column order.
    """
    cols = list(df.columns)

    # Known Chinese column names (akshare returns these in UTF-8 on most systems)
    KNOWN = {
        "关键词": "keyword",
        "新闻标题": "title",
        "新闻内容": "content",
        "发布时间": "time",
        "文章来源": "source",
        "新闻链接": "url",
    }
    # Also try unicode-escaped variants
    KNOWN_ESCAPED = {
        "关键词": "keyword",
        "新闻标题": "title",
        "新闻内容": "content",
        "发布时间": "time",
        "文章来源": "source",
        "新闻链接": "url",
    }

    result = {}
    for i, c in enumerate(cols):
        name = KNOWN.get(c) or KNOWN_ESCAPED.get(c)
        if name:
            result[name] = c
        else:
            # Fall back to positional mapping (known akshare column order)
            POSITIONAL = ["keyword", "title", "content", "time", "source", "url"]
            if i < len(POSITIONAL):
                result[POSITIONAL[i]] = c

    return result


# ── China macro news ──────────────────────────────────────────────────────


def fetch_eastmoney_macro_news(limit: int = 15) -> str:
    """Fetch recent China macro / policy news from Eastmoney.

    Scrapes the Eastmoney macro news homepage. Uses ``requests`` for
    robust encoding handling (the page declares UTF-8 but content may
    be GBK-encoded; requests handles this transparently).

    Returns:
        Formatted markdown string with macro news headlines.
    """
    import re

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
        # Let requests auto-detect encoding from content
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


# ── Combined entry point ──────────────────────────────────────────────────


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
