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

# ── Network note ───────────────────────────────────────────────────────────
# Eastmoney APIs (emweb.securities.eastmoney.com) may be unreachable from
# outside mainland China. All functions below degrade gracefully: they
# return empty lists on connection errors rather than raising. For
# reliable access, deploy behind a China-side proxy or on a domestic VPS.
# The `stock_zh_a_spot_em` endpoint (push2his.eastmoney.com) generally
# has wider reach than the board/industry endpoints.


def _ensure_akshare():
    """Import akshare or raise."""
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


def _bare_code(ticker: str) -> str:
    """Strip exchange suffix: '000933.SZ' → '000933'."""
    return ticker.upper().replace(".SZ", "").replace(".SS", "")


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

    Uses ``stock_board_industry_cons_em`` for constituent list, then
    enriches with real-time metrics from ``stock_zh_a_spot_em``.

    Returns a list of dicts with keys: ticker, name, price, change_pct,
    pe, pb, market_cap, volume, turnover.
    """
    ak = _ensure_akshare()
    results: list[dict] = []

    # Step 1: Get constituent tickers
    try:
        df_cons = ak.stock_board_industry_cons_em(symbol=industry_name)
        if df_cons is None or df_cons.empty:
            return []
        # Columns: 代码, 名称, 最新价, 涨跌幅, 涨跌额, 成交量, ...
        constituent_tickers = set()
        for _, row in df_cons.iterrows():
            code = str(row.iloc[0]) if len(row) > 0 else ""
            if code and re.match(r"^\d{6}$", code):
                constituent_tickers.add(code)
    except Exception as exc:
        logger.warning("Industry constituents failed for %s: %s", industry_name, exc)
        return []

    if not constituent_tickers:
        return []

    # Step 2: Enrich with real-time spot data
    try:
        df_spot = ak.stock_zh_a_spot_em()
        if df_spot is None or df_spot.empty:
            return []
    except Exception as exc:
        logger.warning("Spot data fetch failed: %s", exc)
        return []

    # Build lookup: ticker → row index
    # Columns are: 代码, 名称, 最新价, 涨跌幅, 涨跌额, 成交量, 成交额, 振幅,
    #              换手率, 量比, 市盈率-动态, 市净率, 总市值, 流通市值, ...
    for _, row in df_spot.iterrows():
        try:
            code = str(row.iloc[0]).zfill(6) if len(row) > 0 else ""
        except (ValueError, TypeError):
            continue
        if code not in constituent_tickers:
            continue

        name = str(row.iloc[1]) if len(row) > 1 else ""
        if _is_st_stock(name, code):
            continue

        # Extract metrics safely
        try:
            price = float(row.iloc[2]) if len(row) > 2 else None
        except (ValueError, TypeError):
            price = None
        try:
            change_pct = float(row.iloc[3]) if len(row) > 3 else None
        except (ValueError, TypeError):
            change_pct = None
        try:
            pe = float(row.iloc[10]) if len(row) > 10 else None
        except (ValueError, TypeError):
            pe = None
        try:
            pb = float(row.iloc[11]) if len(row) > 11 else None
        except (ValueError, TypeError):
            pb = None
        try:
            market_cap = float(row.iloc[12]) if len(row) > 12 else None
        except (ValueError, TypeError):
            market_cap = None

        results.append({
            "ticker": f"{code}.{'SH' if code.startswith('6') else 'SZ'}",
            "code": code,
            "name": name,
            "price": price,
            "change_pct": change_pct,
            "pe": pe,
            "pb": pb,
            "market_cap": market_cap,
        })

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

    Args:
        industry: Eastmoney industry name (e.g. "汽车零部件").
        concept: Eastmoney concept board name (e.g. "机器人概念").
        min_market_cap: Minimum total market cap in yuan.
        max_pe: Maximum PE ratio (excludes negative-PE stocks).
        min_roe: Minimum ROE (requires financial snapshot lookup).
        exclude_st: Exclude ST/*ST/delisting-risk stocks.

    Returns:
        List of stock dicts sorted by market cap descending.
    """
    ak = _ensure_akshare()

    # Build candidate ticker set
    candidate_tickers: set[str] = set()

    if industry:
        try:
            df = ak.stock_board_industry_cons_em(symbol=industry)
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = str(row.iloc[0]).zfill(6)
                    candidate_tickers.add(code)
        except Exception as exc:
            logger.warning("Industry '%s' lookup failed: %s", industry, exc)

    if concept:
        try:
            df = ak.stock_board_concept_cons_em(symbol=concept)
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = str(row.iloc[0]).zfill(6)
                    candidate_tickers.add(code)
        except Exception as exc:
            logger.warning("Concept '%s' lookup failed: %s", concept, exc)

    # If no filter specified, screen all A-shares
    screen_all = not candidate_tickers

    # Fetch real-time spot data for all A-shares
    try:
        df_spot = ak.stock_zh_a_spot_em()
        if df_spot is None or df_spot.empty:
            return []
    except Exception as exc:
        logger.warning("Spot data fetch failed: %s", exc)
        return []

    results: list[dict] = []
    for _, row in df_spot.iterrows():
        try:
            code = str(row.iloc[0]).zfill(6)
        except (ValueError, TypeError):
            continue
        if not screen_all and code not in candidate_tickers:
            continue

        name = str(row.iloc[1]) if len(row) > 1 else ""
        if exclude_st and _is_st_stock(name, code):
            continue

        # Extract metrics
        try:
            price = float(row.iloc[2]) if len(row) > 2 else None
        except (ValueError, TypeError):
            price = None
        try:
            change_pct = float(row.iloc[3]) if len(row) > 3 else None
        except (ValueError, TypeError):
            change_pct = None
        try:
            pe = float(row.iloc[10]) if len(row) > 10 else None
        except (ValueError, TypeError):
            pe = None
        try:
            pb = float(row.iloc[11]) if len(row) > 11 else None
        except (ValueError, TypeError):
            pb = None
        try:
            market_cap = float(row.iloc[12]) if len(row) > 12 else None
        except (ValueError, TypeError):
            market_cap = None
        try:
            turnover = float(row.iloc[7]) if len(row) > 7 else None
        except (ValueError, TypeError):
            turnover = None

        # Apply filters
        if market_cap is None or market_cap < min_market_cap:
            continue
        if max_pe is not None and pe is not None and pe > max_pe:
            continue

        suffix = "SH" if code.startswith("6") else "SZ"
        results.append({
            "ticker": f"{code}.{suffix}",
            "code": code,
            "name": name,
            "price": price,
            "change_pct": change_pct,
            "pe": pe,
            "pb": pb,
            "market_cap": market_cap,
            "turnover": turnover,
        })

    # Sort by market cap descending
    results.sort(key=lambda x: x.get("market_cap") or 0, reverse=True)
    return results


def get_financial_snapshots(
    tickers: list[str],
    max_stocks: int = 30,
) -> list[dict]:
    """Get key financial metrics for a list of tickers.

    Fetches ``stock_financial_analysis_indicator`` for each ticker.
    This is relatively slow (~1-2s per stock), so only call for the
    top candidates after initial screening.

    Returns a list of dicts enriched with ROE, ROA, revenue growth,
    net margin, debt ratio, and EPS.
    """
    ak = _ensure_akshare()
    results: list[dict] = []

    for ticker in tickers[:max_stocks]:
        bare = _bare_code(ticker)
        try:
            df = ak.stock_financial_analysis_indicator(symbol=bare, start_year="2023")
            if df is None or df.empty:
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
            continue

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
