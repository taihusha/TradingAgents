"""Chinese social media sentiment fetchers for A-share / China-listed stocks.

Three platforms are supported, each following the same graceful-degradation
pattern used by ``reddit.py`` and ``stocktwits.py``: no exceptions surface,
every function returns a formatted plaintext string (or a placeholder on
failure), and the LLM always sees *something* — real data or a clear
``<platform unavailable>`` marker.

Platforms
---------
* **东方财富股吧** (eastmoney.com) — China's largest retail stock forum.
  Uses Eastmoney's internal JSON API for stock discussion posts, falling
  back to HTML page scraping when the API endpoint changes. Posts carry
  auto-analysed sentiment tags (看多/看空/中性).
* **雪球** (xueqiu.com) — China's top investment-community platform.
  Semi-public JSON API behind a session cookie set by visiting the homepage.
* **同花顺** (10jqka.com.cn) — Stock discussion circles. Tries multiple
  known URL patterns for the discussion list, falling back gracefully.

Auto-detection
--------------
Chinese A-share tickers use numeric codes with ``.SZ`` (Shenzhen) or ``.SS``
(Shanghai) suffixes. Non-CN tickers (e.g. ``AAPL``) are detected and skipped
immediately, returning a clear placeholder.

Configuration
-------------
Sources are opt-in via the ``cn_social_sources`` config key (list), which
defaults to empty (disabled). The combined-entry function
``fetch_cn_social_posts`` respects this config.

.. code:: bash

    # .env:
    TRADINGAGENTS_CN_SOCIAL_SOURCES=eastmoney_guba,xueqiu,10jqka

.. note::

    Chinese financial platforms frequently update their frontend and API
    endpoints. Each fetcher below attempts multiple known patterns and
    degrades gracefully on failure. For production use with guaranteed
    uptime, consider integrating the ``akshare`` library (MIT-licensed,
    ``pip install akshare``) as an optional backend — it wraps hundreds
    of Chinese financial data sources and is maintained by the Chinese
    quant community.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36 "
    "tradingagents/0.2"
)

# ---------------------------------------------------------------------------
# Ticker normalisation for Chinese stocks
# ---------------------------------------------------------------------------

_CN_TICKER_RE = re.compile(r"^(\d{6})(?:\.(SZ|SS))?$", re.IGNORECASE)


def _normalize_cn_ticker(ticker: str) -> Tuple[str, str, str]:
    """Decompose a ticker into (bare_code, exchange, platform_symbol).

    >>> _normalize_cn_ticker("000933.SZ")
    ('000933', 'SZ', 'SZ000933')
    >>> _normalize_cn_ticker("600111.SS")
    ('600111', 'SH', 'SH600111')
    >>> _normalize_cn_ticker("AAPL")
    ('', '', '')
    """
    m = _CN_TICKER_RE.match(ticker.strip().upper())
    if not m:
        return ("", "", "")
    bare = m.group(1)
    suffix = m.group(2)
    if suffix:
        # .SS = Shanghai (SH), .SZ = Shenzhen (SZ)
        exchange = "SH" if suffix.upper() == "SS" else suffix.upper()
    elif bare.startswith("6"):
        exchange = "SH"
    else:
        exchange = "SZ"
    platform_symbol = f"{exchange}{bare}"
    return (bare, exchange, platform_symbol)


def _safe_http_fetch(
    url: str,
    timeout: float = 10.0,
    headers: Optional[dict] = None,
    method: str = "GET",
    data: Optional[bytes] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Fetch a URL and return (body_text, None) or (None, error_description).

    Never raises — always returns a two-tuple so callers can branch cleanly.
    """
    if headers is None:
        headers = {"User-Agent": _UA}
    try:
        req = Request(url, headers=headers, data=data, method=method)
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        # Try UTF-8 first, fall back to GBK (common for Chinese sites).
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("gbk", errors="replace")
        return (text, None)
    except (HTTPError, URLError, TimeoutError) as exc:
        return (None, f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        return (None, f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# 东方财富股吧 (Eastmoney Guba)
# ---------------------------------------------------------------------------

# Strategy 1: Eastmoney's internal JSON API (preferred — structured data).
# The endpoint is reverse-engineered from the mobile Guba app's traffic.
# Strategy 2: HTML page scraping (fallback when the JSON API changes).
_GUBA_API = "https://guba.eastmoney.com/interface/GetData.aspx"
_GUBA_PAGE = "https://guba.eastmoney.com/list,{bare},f.html"


def _build_guba_api_params(bare: str, page: int = 1, count: int = 20) -> dict:
    """Build POST parameters for the Eastmoney Guba data API."""
    return {
        "code": bare,
        "type": "1",
        "page": str(page),
        "count": str(count),
        "sessionid": "",
        "source": "web",
    }


def _parse_guba_json(data: dict, limit: int) -> List[dict]:
    """Parse Eastmoney Guba JSON API response into post dicts."""
    posts: List[dict] = []
    result = data.get("result") or data.get("data") or {}
    post_list = result if isinstance(result, list) else result.get("list", [])

    for item in post_list:
        if not isinstance(item, dict):
            continue
        if len(posts) >= limit:
            break

        title = (item.get("title") or "").replace("\n", " ").strip()
        if not title:
            continue

        # Sentiment: the API may return a "pz" (评值) field.
        pz = item.get("pz", "")
        sentiment_map = {"1": "看多", "-1": "看空", "0": "中性"}
        sentiment_label = sentiment_map.get(str(pz), "")

        posts.append({
            "title": title,
            "author": (item.get("user_name") or item.get("author") or "?"),
            "created_at": (item.get("post_time") or item.get("created_at") or "?"),
            "sentiment": sentiment_label,
            "reply_count": item.get("reply_count", 0),
        })
    return posts


def _parse_guba_html_fallback(html: str, bare: str, limit: int) -> List[dict]:
    """Fallback HTML parser for Eastmoney Guba discussion page.

    The page embeds post data in a ``var article_list = {...}`` block inside
    the server-served HTML (before any client-side React hydration).  This is
    the **primary** and most reliable extraction path — the JSON API endpoint
    is increasingly rate-limited / blocked.
    """
    posts: List[dict] = []

    # ── Strategy A: var article_list (PROVEN working, 2026-06) ──
    # The HTML contains:
    #   <script>var article_list = {"re": [...], "count": ..., ...};</script>
    idx = html.find("var article_list")
    if idx >= 0:
        # Locate the JSON block by tracking brace depth from the opening '{'.
        eq = html.index("=", idx)
        brace = html.index("{", eq)
        depth = 0
        in_str = False
        escape_next = False
        for i in range(brace, len(html)):
            c = html[i]
            if escape_next:
                escape_next = False
                continue
            if c == "\\":
                escape_next = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    raw_json = html[brace : i + 1]
                    try:
                        data = json.loads(raw_json)
                    except (json.JSONDecodeError, ValueError):
                        break  # fall through to other strategies
                    post_list = data.get("re", []) if isinstance(data, dict) else []
                    for item in post_list:
                        if not isinstance(item, dict):
                            continue
                        if len(posts) >= limit:
                            break
                        title = (item.get("post_title") or "").replace("\n", " ").strip()
                        if not title:
                            continue
                        # Sentiment: 1=看多, -1=看空, 0=中性, None=未标记
                        bb = item.get("bullish_bearish")
                        sentiment_map = {1: "看多", -1: "看空", 0: "中性"}
                        sentiment_label = sentiment_map.get(bb, "")
                        posts.append({
                            "title": title,
                            "author": (item.get("user_nickname") or "?"),
                            "created_at": (item.get("post_publish_time") or "?"),
                            "sentiment": sentiment_label,
                            "reply_count": item.get("post_comment_count", 0),
                            "click_count": item.get("post_click_count", 0),
                        })
                    break  # found and processed article_list — done

    # ── Strategy B: __NEXT_DATA__ (Next.js SSR) ──
    if not posts:
        for m in re.finditer(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        ):
            try:
                blob = json.loads(m.group(1))
                props = (
                    blob.get("props", {})
                    .get("pageProps", {})
                    .get("initialData", {})
                    .get("list", [])
                )
                for item in props:
                    if len(posts) >= limit:
                        break
                    if isinstance(item, dict) and item.get("title"):
                        posts.append({
                            "title": item.get("title", ""),
                            "author": item.get("user_name", "?"),
                            "created_at": item.get("post_time", "?"),
                            "sentiment": "",
                            "reply_count": 0,
                            "click_count": 0,
                        })
                if posts:
                    return posts
            except (json.JSONDecodeError, KeyError):
                continue

    # ── Strategy C: __NUXT__ / __INITIAL_STATE__ ──
    if not posts:
        for pattern in [
            r"window\.__INITIAL_STATE__\s*=\s*({.*?});",
            r'<script[^>]*>window\.__NUXT__\s*=\s*({.*?})</script>',
        ]:
            for m in re.finditer(pattern, html, re.DOTALL):
                try:
                    state = json.loads(m.group(1))
                    for path in [
                        ["post", "list"],
                        ["guba", "list"],
                        ["list", "data"],
                    ]:
                        d = state
                        for k in path:
                            d = d.get(k, {}) if isinstance(d, dict) else []
                        if isinstance(d, list) and d:
                            for item in d:
                                if len(posts) >= limit:
                                    break
                                if isinstance(item, dict) and item.get("title"):
                                    posts.append({
                                        "title": item.get("title", ""),
                                        "author": item.get("user_name", "?"),
                                        "created_at": item.get("post_time", "?"),
                                        "sentiment": "",
                                        "reply_count": 0,
                                        "click_count": 0,
                                    })
                            if posts:
                                return posts
                except (json.JSONDecodeError, KeyError):
                    continue

    return posts


def fetch_eastmoney_guba_posts(
    ticker: str,
    limit: int = 20,
    timeout: float = 10.0,
) -> str:
    """Fetch recent Eastmoney Guba discussion posts for a Chinese stock.

    Tries the JSON API first, falls back to HTML page scraping. Returns a
    formatted plaintext block with post titles, authors, timestamps, and
    auto-analysed sentiment labels.
    """
    bare, _exchange, _platform = _normalize_cn_ticker(ticker)
    if not bare:
        return f"<eastmoney guba: {ticker} does not appear to be a Chinese A-share ticker>"

    posts: List[dict] = []
    headers = {
        "User-Agent": _UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://guba.eastmoney.com",
        "Referer": f"https://guba.eastmoney.com/list,{bare},f.html",
    }

    # Strategy 1: JSON API (POST).
    params = _build_guba_api_params(bare, count=limit)
    body = urlencode(params).encode("ascii")
    text, err = _safe_http_fetch(
        _GUBA_API, timeout=timeout, headers=headers, method="POST", data=body
    )
    if text:
        try:
            # Eastmoney returns JSONP occasionally — strip the wrapper.
            jsonp_match = re.match(r"^[a-zA-Z_]\w*\((.*)\)\s*$", text.strip())
            json_text = jsonp_match.group(1) if jsonp_match else text
            data = json.loads(json_text)
            if isinstance(data, dict):
                posts = _parse_guba_json(data, limit)
        except (json.JSONDecodeError, ValueError):
            logger.debug("Guba JSON API parse failed for %s, trying HTML fallback", bare)

    # Strategy 2: HTML page with embedded state extraction.
    if not posts:
        html, page_err = _safe_http_fetch(
            _GUBA_PAGE.format(bare=bare),
            timeout=timeout,
            headers={"User-Agent": _UA, "Accept": "text/html"},
        )
        if html:
            posts = _parse_guba_html_fallback(html, bare, limit)

    # Format output.
    if not posts:
        detail = err or page_err or "no data returned"
        # Only log at debug — this is a normal condition for low-volume stocks.
        logger.debug("Eastmoney Guba: no posts for %s (%s)", bare, detail)
        return f"<no Eastmoney Guba posts found for {bare}>"

    lines = [f"东方财富股吧 — {len(posts)} recent posts for {bare}:"]
    for p in posts:
        sentiment_str = f" · {p['sentiment']}" if p.get("sentiment") else ""
        replies = p.get("reply_count", 0)
        replies_str = f" · {replies} replies" if replies else ""
        lines.append(
            f"  [{p['created_at']} · @{p['author']}{sentiment_str}{replies_str}] {p['title']}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 雪球 (Xueqiu)
# ---------------------------------------------------------------------------

_XUEQIU_SEARCH = "https://xueqiu.com/statuses/search.json"
_XUEQIU_HOME = "https://xueqiu.com/"


def _acquire_xueqiu_cookie(timeout: float = 10.0) -> Optional[str]:
    """Visit Xueqiu homepage to obtain session cookies. Returns xq_a_token."""
    try:
        import requests
    except ImportError:
        logger.debug("requests not available for Xueqiu")
        return None

    try:
        sess = requests.Session()
        sess.get(
            _XUEQIU_HOME,
            headers={"User-Agent": _UA},
            timeout=timeout,
        )
        token = sess.cookies.get("xq_a_token", domain=".xueqiu.com")
        if not token:
            # Try the session cookie dict.
            for c in sess.cookies:
                if "token" in c.name.lower():
                    token = c.value
                    break
        return token or None
    except Exception as exc:
        logger.debug("Xueqiu cookie acquisition failed: %s", exc)
        return None


def fetch_xueqiu_posts(
    ticker: str,
    limit: int = 20,
    timeout: float = 10.0,
) -> str:
    """Fetch recent Xueqiu posts for a Chinese stock.

    Xueqiu is China's largest investment-community platform. Posts tend to
    be longer-form and higher-quality than Eastmoney Guba.
    """
    bare, _exchange, platform_symbol = _normalize_cn_ticker(ticker)
    if not bare:
        return f"<xueqiu: {ticker} does not appear to be a Chinese A-share ticker>"

    cookie = _acquire_xueqiu_cookie(timeout=timeout)
    if not cookie:
        return f"<xueqiu unavailable for {bare}: could not obtain session cookie>"

    try:
        import requests
    except ImportError:
        return f"<xueqiu unavailable for {bare}: requests library not available>"

    params = {
        "count": limit,
        "comment": 0,
        "symbol": platform_symbol,
        "hl": 0,
        "source": "all",
        "sort": "time",
        "page": 1,
    }
    headers = {
        "User-Agent": _UA,
        "Accept": "application/json",
        "Cookie": f"xq_a_token={cookie}",
        "Origin": "https://xueqiu.com",
        "Referer": "https://xueqiu.com/",
    }

    try:
        resp = requests.get(
            _XUEQIU_SEARCH, params=params, headers=headers, timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("Xueqiu API call failed for %s: %s", ticker, exc)
        return f"<xueqiu unavailable for {bare}: {type(exc).__name__}>"

    post_list = data.get("list", []) if isinstance(data, dict) else []
    if not post_list:
        return f"<no Xueqiu posts found for {platform_symbol}>"

    lines = [f"雪球 — {min(len(post_list), limit)} recent posts for {platform_symbol}:"]
    for item in post_list[:limit]:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").replace("\n", " ").strip()
        text = (item.get("text") or "").replace("\n", " ").strip()
        if len(text) > 200:
            text = text[:200] + "…"
        created = item.get("created_at")
        if created:
            try:
                created_str = datetime.fromtimestamp(
                    created / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError, OSError):
                created_str = "?"
        else:
            created_str = "?"
        screen_name = (
            (item.get("user") or {}).get("screen_name", "?")
            if isinstance(item.get("user"), dict)
            else "?"
        )
        reply_count = item.get("reply_count", 0)
        lines.append(
            f"  [{created_str} · @{screen_name} · {reply_count} replies] {title}"
        )
        if text:
            lines.append(f"    {text}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 同花顺 (10jqka)
# ---------------------------------------------------------------------------

# 10jqka has gone through several URL pattern changes. We try multiple
# known patterns; the current one is the "group" URL.
_10JQKA_URLS = [
    "https://t.10jqka.com.cn/group/{bare}/",       # primary
    "https://t.10jqka.com.cn/circle/single/_{bare}/",  # legacy
]


def _parse_10jqka_html(html: str, limit: int) -> List[dict]:
    """Extract posts from 10jqka discussion page HTML.

    10jqka pages are SPA-rendered (React) so the server HTML may contain
    minimal content. We look both at visible text and any embedded JSON state.
    """
    posts: List[dict] = []

    # Strategy 1: Look for JSON state blobs (SSR/Next.js style).
    for pattern in [
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        r"window\.__INITIAL_STATE__\s*=\s*({.*?});",
        r'<script[^>]*>window\.pageData\s*=\s*({.*?})</script>',
    ]:
        for m in re.finditer(pattern, html, re.DOTALL):
            try:
                blob = json.loads(m.group(1))
                # Walk common state shapes for post lists.
                for path in [
                    ["props", "pageProps", "data", "list"],
                    ["data", "feeds"],
                    ["list"],
                    ["feeds"],
                ]:
                    d = blob
                    for k in path:
                        if isinstance(d, dict):
                            d = d.get(k, {})
                        else:
                            d = {}
                    if isinstance(d, list):
                        for item in d:
                            if len(posts) >= limit:
                                break
                            if isinstance(item, dict):
                                title = (item.get("title") or item.get("text") or "")
                                title = re.sub(r"<[^>]+>", "", title).strip()
                                if title:
                                    posts.append({
                                        "title": title,
                                        "content": "",
                                        "created_at": item.get("created_at")
                                        or item.get("time")
                                        or "?",
                                    })
                        if posts:
                            return posts
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    # Strategy 2: Scan visible HTML for article-like structures.
    # Match common 10jqka discussion card patterns.
    card_patterns = [
        r'<div[^>]*class="[^"]*discuss-list[^"]*"[^>]*>(.*?)</div>\s*</div>',
        r'<div[^>]*class="[^"]*article[^"]*"[^>]*>(.*?)</div>\s*</div>',
        r'<li[^>]*class="[^"]*item[^"]*"[^>]*>(.*?)</li>',
    ]
    for pattern in card_patterns:
        blocks = re.findall(pattern, html, re.DOTALL)
        for block in blocks:
            if len(posts) >= limit:
                break
            # Extract title from <a> tags.
            title_m = re.search(r'<a[^>]*>(.*?)</a>', block, re.DOTALL)
            if title_m:
                title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
                if title and len(title) >= 2:
                    time_m = re.search(
                        r'(?:20\d{2}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)',
                        block,
                    )
                    posts.append({
                        "title": title,
                        "content": "",
                        "created_at": time_m.group(1) if time_m else "?",
                    })
        if posts:
            break

    return posts


def fetch_10jqka_posts(
    ticker: str,
    limit: int = 20,
    timeout: float = 10.0,
) -> str:
    """Fetch recent 同花顺 (10jqka) discussion posts for a Chinese stock."""
    bare, _exchange, _platform = _normalize_cn_ticker(ticker)
    if not bare:
        return f"<10jqka: {ticker} does not appear to be a Chinese A-share ticker>"

    posts: List[dict] = []
    last_error = None

    for url_template in _10JQKA_URLS:
        url = url_template.format(bare=bare)
        html, err = _safe_http_fetch(
            url,
            timeout=timeout,
            headers={"User-Agent": _UA, "Accept": "text/html"},
        )
        if html:
            posts = _parse_10jqka_html(html, limit)
            if posts:
                break
        if err:
            last_error = err

    if not posts:
        detail = last_error or "no data"
        logger.debug("10jqka: no posts for %s (%s)", bare, detail)
        return f"<no 10jqka posts found for {bare}>"

    lines = [f"同花顺 — {len(posts)} recent posts for {bare}:"]
    for p in posts:
        lines.append(f"  [{p['created_at']}] {p['title']}")
        if p.get("content"):
            body = (
                p["content"][:200] + "…"
                if len(p["content"]) > 200
                else p["content"]
            )
            lines.append(f"    {body}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 东方财富情绪指标 (Eastmoney Sentiment Metrics via akshare)
# ---------------------------------------------------------------------------

def fetch_eastmoney_sentiment_metrics(
    ticker: str,
    limit: int = 0,   # ignored — included for uniform calling convention
    timeout: float = 10.0,
) -> str:
    """Fetch quantitative sentiment metrics from Eastmoney via akshare.

    Returns structured metrics:
    - 参与意愿 (participation willingness) — recent 5 days
    - 用户关注指数 (user attention index) — recent 30 days
    - 综合评分 (composite rating) — recent 30 days
    - 机构参与度 (institutional participation) — recent 42 days

    These are numeric time-series that complement the textual Guba posts.
    Falls back gracefully if akshare is not installed or an endpoint changes.
    """
    bare, _exchange, _platform = _normalize_cn_ticker(ticker)
    if not bare:
        return f"<eastmoney sentiment: {ticker} is not a Chinese A-share ticker>"

    try:
        import akshare as ak
    except ImportError:
        return "<eastmoney sentiment metrics unavailable: akshare not installed>"

    blocks: List[str] = []

    # 1. Participation willingness (参与意愿) — last 5 trading days
    try:
        df = ak.stock_comment_detail_scrd_desire_em(symbol=bare)
        if df is not None and len(df) > 0:
            recent = df.tail(5)
            lines = [f"📊 参与意愿 (近5个交易日):"]
            for _, row in recent.iterrows():
                date = str(row.get("交易日期", "?"))
                desire = row.get("参与意愿", "N/A")
                change = row.get("参与意愿变化", "")
                change_str = f" ({change:+.2f})" if change != "" else ""
                lines.append(f"  {date}: {desire}{change_str}")
            blocks.append("\n".join(lines))
    except Exception as e:
        logger.debug("akshare desire_em failed for %s: %s", bare, e)

    # 2. User attention index (关注指数) — latest value
    try:
        df = ak.stock_comment_detail_scrd_focus_em(symbol=bare)
        if df is not None and len(df) > 0:
            latest = df.iloc[-1]
            prev5 = df.iloc[-6] if len(df) >= 6 else df.iloc[0]
            latest_val = latest.get("用户关注指数", "N/A")
            prev_val = prev5.get("用户关注指数", latest_val)
            try:
                trend = "↑" if float(latest_val) > float(prev_val) else "↓"
            except (ValueError, TypeError):
                trend = ""
            blocks.append(
                f"📊 用户关注指数: {latest_val} {trend} (前一交易日: {prev_val})"
            )
    except Exception as e:
        logger.debug("akshare focus_em failed for %s: %s", bare, e)

    # 3. Composite rating (综合评分) — latest value
    try:
        df = ak.stock_comment_detail_zhpj_lspf_em(symbol=bare)
        if df is not None and len(df) > 0:
            latest = df.iloc[-1]
            prev5 = df.iloc[-6] if len(df) >= 6 else df.iloc[0]
            latest_val = latest.get("评分", "N/A")
            prev_val = prev5.get("评分", latest_val)
            try:
                trend = "↑" if float(latest_val) > float(prev_val) else "↓"
            except (ValueError, TypeError):
                trend = ""
            blocks.append(
                f"📊 综合评分: {latest_val:.2f} {trend} (5日前: {prev_val:.2f})"
            )
    except Exception as e:
        logger.debug("akshare zhpj_lspf_em failed for %s: %s", bare, e)

    # 4. Institutional participation (机构参与度)
    try:
        df = ak.stock_comment_detail_zlkp_jgcyd_em(symbol=bare)
        if df is not None and len(df) > 0:
            latest = df.iloc[-1]
            latest_val = latest.get("机构参与度", "N/A")
            blocks.append(
                f"📊 机构参与度: {latest_val:.2f} (最新交易日)"
            )
    except Exception as e:
        logger.debug("akshare jgcyd_em failed for %s: %s", bare, e)

    if not blocks:
        return f"<eastmoney sentiment metrics: no data for {bare}>"

    header = f"🔢 东方财富定量情绪指标 — {bare}:"
    return header + "\n" + "\n".join(blocks)


# ---------------------------------------------------------------------------
# Combined convenience entry point
# ---------------------------------------------------------------------------

_SOURCE_FETCHERS = {
    "eastmoney_guba": fetch_eastmoney_guba_posts,
    "eastmoney_sentiment": fetch_eastmoney_sentiment_metrics,
    "xueqiu": fetch_xueqiu_posts,
    "10jqka": fetch_10jqka_posts,
}


def fetch_cn_social_posts(
    ticker: str,
    sources: Optional[List[str]] = None,
    limit_per_source: int = 20,
    timeout: float = 10.0,
) -> str:
    """Fetch Chinese social media sentiment data from all enabled sources.

    Parameters
    ----------
    ticker : str
        Stock ticker (e.g. ``"000933.SZ"``). Non-CN tickers are detected.
    sources : list of str or None
        When ``None``, reads the ``cn_social_sources`` config key.
        An empty list disables all Chinese social fetching.
    limit_per_source : int
        Max posts per platform (default 20).
    timeout : float
        HTTP timeout in seconds per platform request.

    Returns
    -------
    str
        Combined plaintext block for prompt injection, or ``""`` when no
        sources are enabled / all return empty.
    """
    if sources is None:
        try:
            from tradingagents.dataflows.config import get_config
            cfg = get_config()
            sources = cfg.get("cn_social_sources", [])
        except Exception:
            return ""

    if not sources:
        return ""

    blocks: List[str] = []
    for name in sources:
        fetcher = _SOURCE_FETCHERS.get(name)
        if fetcher is None:
            logger.debug("Unknown Chinese social source: %s", name)
            continue
        try:
            result = fetcher(ticker, limit=limit_per_source, timeout=timeout)
        except Exception as exc:
            logger.warning(
                "Chinese social fetcher %s raised for %s: %s",
                name, ticker, exc,
            )
            result = f"<{name} error for {ticker}: {type(exc).__name__}>"
        if result:
            blocks.append(result)

    if not blocks:
        return ""

    return "\n\n---\n\n".join(blocks)
