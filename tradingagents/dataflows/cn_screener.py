"""A-share stock screening and discovery data layer.

Provides industry/theme-based stock screening via akshare, plus
financial snapshot fetching for candidate ranking. Designed as the
data backend for the Discovery Analyst and cross-stock comparison.

Key functions:
- ``get_industry_list()`` — all Eastmoney industry names & codes
- ``get_industry_stocks()`` — stocks in an industry with key metrics
- ``screen_stocks()`` — multi-criteria A-share screening
- ``get_financial_snapshots()`` — batch financial metrics for ranking
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Minimum meaningful market cap (yuan) — filters out shell/micro-cap noise
DEFAULT_MIN_MARKET_CAP = 5_000_000_000  # 5B

# ── Browser TLS session injection (shared across all akshare modules) ────────
# Replaces requests.Session with curl_cffi.Session (Chrome 110 TLS fingerprint)
# or falls back to UA header injection.  Import-only side effect — safe to
# repeat across modules (one-shot patch).
from ._browser_session import inject_browser_session  # noqa: E402, F401


def _ensure_akshare():
    """Import akshare or raise (browser UA already patched at import time)."""
    try:
        import akshare as ak
        return ak
    except ImportError:
        raise ImportError("akshare is not installed. Install: pip install akshare")


def _is_st_stock(name: str, ticker: str) -> bool:
    """Detect ST / *ST / delisting-risk stocks."""
    if "ST" in str(name).upper() or "*ST" in str(name).upper():
        return True
    if "退" in str(name):
        return True
    return False


def _safe_float(row, col_idx: int) -> float | None:
    """Extract a float from a pandas row by column index, returning None on failure."""
    try:
        if col_idx >= len(row):
            return None
        v = row.iloc[col_idx]
        return float(v) if v is not None and str(v) not in ("nan", "", "None") else None
    except (ValueError, TypeError):
        return None


def _bare_code(ticker: str) -> str:
    """Strip exchange suffix: '000933.SZ' → '000933'."""
    return ticker.upper().replace(".SZ", "").replace(".SS", "")


def _parse_constituent_df(
    df,
    min_market_cap: float,
    exclude_st: bool,
    codes_seen: set[str] | None = None,
) -> list[dict]:
    """Parse an industry/concept constituent DataFrame into stock dicts.

    Columns expected (``stock_board_industry_cons_em`` / ``stock_board_concept_cons_em``):
      0: 代码  1: 名称  2: 最新价  3: 涨跌幅  6: 换手率  12: 总市值

    Returns list of dicts with ticker, code, name, price, change_pct,
    market_cap, turnover.  PE/PB are set to None (not available from
    the constituent API — callers that need them must enrich separately).
    """
    results: list[dict] = []
    if codes_seen is None:
        codes_seen = set()

    for _, row in df.iterrows():
        code = str(row.iloc[0]).zfill(6)
        if code in codes_seen:
            continue
        codes_seen.add(code)

        name = str(row.iloc[1]) if len(row) > 1 else ""
        if exclude_st and _is_st_stock(name, code):
            continue

        market_cap = _safe_float(row, 12)
        if market_cap is None or market_cap < min_market_cap:
            continue

        suffix = "SH" if code.startswith("6") else "SZ"
        results.append({
            "ticker": f"{code}.{suffix}",
            "code": code,
            "name": name,
            "price": _safe_float(row, 2),
            "change_pct": _safe_float(row, 3),
            "pe": None,       # not in constituent API
            "pb": None,       # not in constituent API
            "market_cap": market_cap,
            "turnover": _safe_float(row, 6),
        })

    return results


def get_industry_list() -> list[dict[str, str]]:
    """Get all Eastmoney industry classification names and codes.

    Returns a list of ``{"name": "汽车零部件", "code": "BK0481"}`` dicts.
    """
    ak = _ensure_akshare()
    try:
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return []
        results = []
        for _, row in df.iterrows():
            results.append({
                "name": str(row.iloc[0]) if len(row) > 0 else "",
                "code": str(row.iloc[1]) if len(row) > 1 else "",
            })
        return results
    except Exception as exc:
        logger.warning("Failed to get industry list: %s", exc)
        return []


def get_industry_stocks(industry_name: str) -> list[dict]:
    """Get all stocks in an Eastmoney industry with basic metrics.

    Uses ``stock_board_industry_cons_em`` directly — the constituent API
    already includes price, change%, market cap, and turnover.  PE and PB
    are NOT available from this endpoint; callers that need them should
    enrich via ``get_financial_snapshots()`` or a targeted spot-data lookup.

    Returns a list of dicts with keys: ticker, name, price, change_pct,
    pe (None), pb (None), market_cap, turnover.
    """
    ak = _ensure_akshare()
    try:
        df = ak.stock_board_industry_cons_em(symbol=industry_name)
        if df is None or df.empty:
            return []
    except Exception as exc:
        logger.warning("Industry constituents failed for %s: %s", industry_name, exc)
        return []

    results = _parse_constituent_df(
        df,
        min_market_cap=0,   # no min-cap filter for raw listing
        exclude_st=True,
    )
    results.sort(key=lambda x: x.get("market_cap") or 0, reverse=True)
    return results


def screen_stocks(
    industry: Optional[str] = None,
    concept: Optional[str] = None,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP,
    max_pe: Optional[float] = 200,
    min_roe: Optional[float] = None,
    exclude_st: bool = True,
) -> list[dict]:
    """Screen A-share stocks by multiple criteria.

    When *industry* or *concept* is given, data is sourced directly from
    the Eastmoney board constituent API (fast: one HTTP call per board).
    ``stock_zh_a_spot_em`` (all ~5000 A-shares) is only used for
    broad-market screening when neither filter is provided.

    Note: PE/PB filtering is only available in broad-market mode because
    the constituent API does not include PE/PB columns.

    Args:
        industry: Eastmoney industry name (e.g. "汽车零部件").
        concept: Eastmoney concept board name (e.g. "机器人概念").
        min_market_cap: Minimum total market cap in yuan.
        max_pe: Maximum PE ratio (broad-market mode only).
        min_roe: Minimum ROE (requires financial snapshot lookup).
        exclude_st: Exclude ST/*ST/delisting-risk stocks.

    Returns:
        List of stock dicts sorted by market cap descending.
    """
    ak = _ensure_akshare()
    results: list[dict] = []
    codes_seen: set[str] = set()
    has_filter = bool(industry or concept)
    constituent_ok = False

    # ── Fast path: industry / concept constituent data ──
    if industry:
        try:
            df = ak.stock_board_industry_cons_em(symbol=industry)
            if df is not None and not df.empty:
                results.extend(
                    _parse_constituent_df(df, min_market_cap, exclude_st, codes_seen)
                )
                constituent_ok = True
        except Exception as exc:
            logger.warning("Industry '%s' lookup failed: %s", industry, exc)

    if concept:
        try:
            df = ak.stock_board_concept_cons_em(symbol=concept)
            if df is not None and not df.empty:
                results.extend(
                    _parse_constituent_df(df, min_market_cap, exclude_st, codes_seen)
                )
                constituent_ok = True
        except Exception as exc:
            logger.warning("Concept '%s' lookup failed: %s", concept, exc)

    if constituent_ok:
        results.sort(key=lambda x: x.get("market_cap") or 0, reverse=True)
        return results

    # ── Fallback: if a filter was given but constituent API(s) failed,
    #     fall through to the broad-market path with ticker-based filtering ──
    if has_filter and not constituent_ok:
        if not codes_seen:
            # Both constituent APIs failed — we have no ticker list to filter with.
            # Returning all ~5000 unfiltered stocks is useless; fail cleanly.
            logger.warning(
                "Both industry/concept constituent APIs are unreachable. "
                "Eastmoney board endpoints (emweb.securities.eastmoney.com) "
                "may be blocked from your region. Consider running behind a "
                "mainland China proxy or on a domestic VPS."
            )
            return []
        logger.info(
            "Constituent API partially failed; "
            "falling back to full-market scan with %d known tickers",
            len(codes_seen),
        )

    # ── Broad-market path: scan all A-shares ──
    try:
        df_spot = ak.stock_zh_a_spot_em()
        if df_spot is None or df_spot.empty:
            return []
    except Exception as exc:
        logger.warning("Spot data fetch failed: %s", exc)
        if has_filter:
            logger.warning(
                "Both constituent and spot APIs are unreachable. "
                "Eastmoney endpoints may be blocked from your region. "
                "Consider running behind a mainland China proxy or on a domestic VPS."
            )
        return []

    for _, row in df_spot.iterrows():
        try:
            code = str(row.iloc[0]).zfill(6)
        except (ValueError, TypeError):
            continue
        if codes_seen and code not in codes_seen:
            continue

        name = str(row.iloc[1]) if len(row) > 1 else ""
        if exclude_st and _is_st_stock(name, code):
            continue

        market_cap = _safe_float(row, 12)
        if market_cap is None or market_cap < min_market_cap:
            continue

        pe = _safe_float(row, 10)
        if max_pe is not None and pe is not None and pe > max_pe:
            continue

        suffix = "SH" if code.startswith("6") else "SZ"
        results.append({
            "ticker": f"{code}.{suffix}",
            "code": code,
            "name": name,
            "price": _safe_float(row, 2),
            "change_pct": _safe_float(row, 3),
            "pe": pe,
            "pb": _safe_float(row, 11),
            "market_cap": market_cap,
            "turnover": _safe_float(row, 7),
        })

    results.sort(key=lambda x: x.get("market_cap") or 0, reverse=True)
    return results


def get_financial_snapshots(
    tickers: list[str],
    max_stocks: int = 30,
) -> list[dict]:
    """Get key financial metrics for a list of tickers.

    Fetches ``stock_financial_analysis_indicator`` for each ticker.
    This is relatively slow (~1-2s per stock, sequential), so only call
    for the top candidates after initial screening.  Progress is logged
    at the INFO level.

    Returns a list of dicts enriched with ROE, ROA, revenue growth,
    net margin, debt ratio, and EPS.
    """
    ak = _ensure_akshare()
    results: list[dict] = []
    batch = tickers[:max_stocks]
    total = len(batch)

    for i, ticker in enumerate(batch):
        bare = _bare_code(ticker)
        try:
            df = ak.stock_financial_analysis_indicator(symbol=bare, start_year="2023")
            if df is None or df.empty:
                if total <= 10 or (i + 1) % 10 == 0:
                    logger.info("  Financial snapshots: %d/%d", i + 1, total)
                continue
            latest = df.iloc[-1]

            def _f(col_name: str):
                try:
                    v = latest.get(col_name)
                    return float(v) if v is not None and str(v) not in ("nan", "") else None
                except (ValueError, TypeError):
                    return None

            results.append({
                "ticker": ticker,
                "code": bare,
                "roe": _f("净资产收益率(%)"),
                "roa": _f("总资产收益率(%)"),
                "revenue_growth": _f("主营业务收入增长率(%)"),
                "net_profit_growth": _f("净利润增长率(%)"),
                "net_margin": _f("销售净利率(%)"),
                "gross_margin": _f("销售毛利率(%)"),
                "debt_to_asset": _f("资产负债率(%)"),
                "eps": _f("摊薄每股收益(元)"),
                "bvps": _f("每股净资产_调整前(元)"),
                "current_ratio": _f("流动比率"),
                "quick_ratio": _f("速动比率"),
                "report_date": str(latest.iloc[0]) if len(latest) > 0 else "?",
            })
        except Exception as exc:
            logger.debug("Financial snapshot failed for %s: %s", ticker, exc)

        if total > 5 and (i + 1) % 5 == 0:
            logger.info("  Financial snapshots: %d/%d (%d ok)", i + 1, total, len(results))

    if total > 0:
        logger.info("  Financial snapshots complete: %d/%d succeeded", len(results), total)
    return results


def build_screening_table(
    stocks: list[dict],
    financials: list[dict],
    top_n: int = 30,
) -> str:
    """Build a markdown table combining spot data and financial snapshots.

    Used as the input prompt for the Discovery Analyst LLM evaluation.
    """
    # Index financial data by ticker
    fin_map: dict[str, dict] = {}
    for f in financials:
        fin_map[f["ticker"]] = f

    lines = [
        f"## 候选标的筛选结果（前 {min(len(stocks), top_n)} 只）",
        "",
        "| # | 代码 | 名称 | 市值(亿) | PE | PB | ROE% | 收入增速% | 净利率% | 负债率% |",
        "|---|------|------|----------|----|----|------|----------|---------|--------|",
    ]

    for i, s in enumerate(stocks[:top_n]):
        f = fin_map.get(s["ticker"], {})
        mc = f"{s['market_cap'] / 1e8:.0f}" if s.get("market_cap") else "?"
        pe = f"{s['pe']:.1f}" if s.get("pe") else "?"
        pb = f"{s['pb']:.1f}" if s.get("pb") else "?"
        roe = f"{f.get('roe'):.1f}" if f.get("roe") is not None else "?"
        rev_g = f"{f.get('revenue_growth'):.1f}" if f.get("revenue_growth") is not None else "?"
        net_m = f"{f.get('net_margin'):.1f}" if f.get("net_margin") is not None else "?"
        debt = f"{f.get('debt_to_asset'):.1f}" if f.get("debt_to_asset") is not None else "?"

        lines.append(
            f"| {i+1} | {s['code']} | {s['name']} | {mc} | {pe} | {pb} | "
            f"{roe} | {rev_g} | {net_m} | {debt} |"
        )

    return "\n".join(lines)
