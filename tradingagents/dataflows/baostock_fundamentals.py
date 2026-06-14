"""A-share fundamental data via Baostock — free, stable, no anti-bot issues.

Baostock is a free, registration-free data service with its own servers.
Unlike akshare (which scrapes Eastmoney and battles WAF/anti-bot), Baostock
provides a stable API that never blocks you.  The trade-off: no real-time
quotes, lower update frequency on fundamentals.

Functions match the akshare vendor signatures so they can be registered
as a drop-in fallback in ``interface.py``.

Per-run cache
-------------
The 4 fundamental tools (get_fundamentals, get_balance_sheet, get_cashflow,
get_income_statement) are called sequentially by the fundamentals analyst.
Without caching, each would independently login → query 4-5 tables → logout,
wasting ~3x overhead on repeated queries.  The module-level ``_bs_cache``
holds one comprehensive result per (ticker, curr_date) so all 4 tools share
a single round-trip to the baostock server.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime

from .symbol_utils import NoMarketDataError

logger = logging.getLogger(__name__)

_CN_TICKER_RE = re.compile(r"^(\d{6})(?:\.(SZ|SS))?$", re.IGNORECASE)

# ── Per-run cache: one comprehensive fetch serves all 4 fundamental tools ──
# Key: (ticker, curr_date), Value: dict with keys "fundamentals", "balance",
# "cashflow", "income" — each holding the formatted string or None.
_bs_cache: dict[tuple[str, str], dict[str, str | None]] = {}
_bs_cache_lock = threading.Lock()


def _is_cn_ticker(ticker: str) -> bool:
    """Return True when *ticker* looks like a Chinese A-share code."""
    return bool(_CN_TICKER_RE.match(ticker.strip().upper()))


def _bs_code(ticker: str) -> str:
    """Convert '000933.SZ' → 'sz.000933'."""
    t = ticker.upper()
    if t.endswith(".SZ"):
        return f"sz.{t[:-3]}"
    if t.endswith(".SS"):
        return f"sh.{t[:-3]}"
    bare = t.replace(".SZ", "").replace(".SS", "")
    return f"sh.{bare}" if bare.startswith("6") else f"sz.{bare}"


def _ensure_login() -> bool:
    """Login to baostock (idempotent)."""
    import baostock as bs
    result = bs.login()
    if result.error_code != "0":
        logger.warning("Baostock login failed: %s %s", result.error_code, result.error_msg)
    return result.error_code == "0"


def _logout():
    """Logout from baostock (best-effort)."""
    try:
        import baostock as bs
        bs.logout()
    except Exception:
        pass


def _safe_float(value) -> float | None:
    """Convert baostock string value to float, returning None on failure."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _first_row(query_result, code: str) -> dict | None:
    """Extract first matching row from a baostock query result as a dict."""
    while query_result.next():
        row = query_result.get_row_data()
        if row and row[0] == code:
            return dict(zip(query_result.fields, row))
    return None


def _query_latest(bs, query_fn, code: str, curr_date: str = None) -> tuple[dict | None, str, int]:
    """Query latest available quarter, falling back through quarters and years.

    Returns (row_dict, pub_date_str, year).
    """
    year = int(curr_date[:4]) if curr_date and len(curr_date) >= 4 else datetime.now().year

    for try_year in (year, year - 1):
        for q in (4, 3, 2, 1):
            result = query_fn(bs, code, try_year, q)
            row = _first_row(result, code)
            if row:
                return row, row.get("pubDate", row.get("statDate", "?")), try_year
    return None, "?", year


# ── Core: single comprehensive fetch (login once, query all, logout, cache) ──


def _fetch_all_bs_data(ticker: str, curr_date: str = None) -> dict[str, str | None]:
    """Fetch all fundamental data from Baostock in one login→query→logout cycle.

    Returns a dict with keys "fundamentals", "balance_sheet", "cashflow",
    "income_statement".  Cached per (ticker, curr_date) so the 4 fundamental
    tools share a single round-trip.
    """
    cache_key = (ticker.upper(), curr_date or "")
    with _bs_cache_lock:
        if cache_key in _bs_cache:
            logger.debug("Baostock cache hit for %s", ticker)
            return _bs_cache[cache_key]

    result: dict[str, str | None] = {
        "fundamentals": None,
        "balance_sheet": None,
        "cashflow": None,
        "income_statement": None,
    }

    code = _bs_code(ticker)
    if not _ensure_login():
        with _bs_cache_lock:
            _bs_cache[cache_key] = result
        return result

    try:
        import baostock as bs

        # ── Query all 4 tables in one session ──
        def _profit(bs, c, y, q):
            return bs.query_profit_data(code=c, year=y, quarter=q)

        def _growth(bs, c, y, q):
            return bs.query_growth_data(code=c, year=y, quarter=q)

        def _balance(bs, c, y, q):
            return bs.query_balance_data(code=c, year=y, quarter=q)

        def _ops(bs, c, y, q):
            return bs.query_operation_data(code=c, year=y, quarter=q)

        p_row, pub, used_year = _query_latest(bs, _profit, code, curr_date)
        if p_row is None:
            logger.debug("Baostock: no profitability data for %s", ticker)
        else:
            g_row, _, _ = _query_latest(bs, _growth, code, curr_date)
            b_row, _, _ = _query_latest(bs, _balance, code, curr_date)
            o_row, _, _ = _query_latest(bs, _ops, code, curr_date)

            # ── Extract all metrics (one pass, used by all 4 outputs) ──
            roe = _safe_float(p_row.get("roeAvg"))
            net_margin = _safe_float(p_row.get("npMargin"))
            gross_margin = _safe_float(p_row.get("gpMargin"))
            eps = _safe_float(p_row.get("epsTTM"))
            net_profit = _safe_float(p_row.get("netProfit"))
            revenue = _safe_float(p_row.get("MBRevenue"))

            profit_growth = _safe_float(g_row.get("YOYNI")) if g_row else None
            asset_growth = _safe_float(g_row.get("YOYAsset")) if g_row else None
            equity_growth = _safe_float(g_row.get("YOYEquity")) if g_row else None

            current_ratio = _safe_float(b_row.get("currentRatio")) if b_row else None
            quick_ratio = _safe_float(b_row.get("quickRatio")) if b_row else None
            equity_multiplier = _safe_float(b_row.get("assetToEquity")) if b_row else None

            debt_ratio = None
            if equity_multiplier and equity_multiplier > 0:
                debt_ratio = 1.0 - (1.0 / equity_multiplier)

            asset_turnover = _safe_float(o_row.get("AssetTurnRatio")) if o_row else None

            # ── Formatting helpers ──
            report_label = str(pub)[:10] if pub and len(str(pub)) >= 10 else "?"

            def _pct(value, decimals=2):
                if value is None:
                    return "?"
                return f"{value * 100:.{decimals}f}%"

            def _abs_big(value):
                if value is None:
                    return "?"
                abs_v = abs(value)
                if abs_v >= 1e8:
                    return f"{value / 1e8:.2f} 亿"
                if abs_v >= 1e4:
                    return f"{value / 1e4:.2f} 万"
                return f"{value:.2f}"

            # ── Build fundamentals report ──
            flines = [
                f"## Baostock Fundamentals — {ticker}",
                "",
                f"**Report Period**: {report_label}",
                "",
                "| Metric | Value |",
                "|--------|-------|",
            ]
            flines.append(f"| ROE | {_pct(roe)} |")
            flines.append(f"| Net Margin | {_pct(net_margin)} |")
            flines.append(f"| Gross Margin | {_pct(gross_margin)} |")
            flines.append(f"| EPS (TTM) | {eps:.4f} 元" if eps is not None else "| EPS (TTM) | ? |")
            if net_profit is not None:
                flines.append(f"| Net Profit | {_abs_big(net_profit)} |")
            if revenue is not None:
                flines.append(f"| Revenue | {_abs_big(revenue)} |")
            flines.append(f"| Net Profit Growth (YoY) | {_pct(profit_growth)} |")
            flines.append(f"| Asset Growth (YoY) | {_pct(asset_growth)} |")
            flines.append(f"| Equity Growth (YoY) | {_pct(equity_growth)} |")
            flines.append(f"| Current Ratio | {current_ratio:.2f}" if current_ratio is not None else "| Current Ratio | ?")
            flines.append(f"| Quick Ratio | {quick_ratio:.2f}" if quick_ratio is not None else "| Quick Ratio | ?")
            flines.append(f"| Debt to Assets | {_pct(debt_ratio)} |")
            flines.append(f"| Asset Turnover | {asset_turnover:.2f}" if asset_turnover is not None else "| Asset Turnover | ?")
            flines.append(f"| Equity Multiplier | {equity_multiplier:.2f}x" if equity_multiplier is not None else "| Equity Multiplier | ?")
            flines.append("")
            flines.append("*(Data: Baostock — free, stable, registration-free)*")
            result["fundamentals"] = "\n".join(flines)

            # ── Build balance sheet (subset of same data) ──
            blines = [
                f"## Baostock Balance Sheet — {ticker}",
                "",
                f"**Report Period**: {report_label}",
                "",
                "| Metric | Value |",
                "|--------|-------|",
            ]
            blines.append(f"| Current Ratio | {current_ratio:.2f}" if current_ratio is not None else "| Current Ratio | ?")
            blines.append(f"| Quick Ratio | {quick_ratio:.2f}" if quick_ratio is not None else "| Quick Ratio | ?")
            blines.append(f"| Debt to Assets | {_pct(debt_ratio)} |")
            blines.append(f"| Equity Multiplier | {equity_multiplier:.2f}x" if equity_multiplier is not None else "| Equity Multiplier | ?")
            blines.append(f"| Asset Growth (YoY) | {_pct(asset_growth)} |")
            blines.append(f"| Equity Growth (YoY) | {_pct(equity_growth)} |")
            blines.append("")
            blines.append("*(Data: Baostock)*")
            result["balance_sheet"] = "\n".join(blines)

            # ── Build income statement (subset of same data) ──
            ilines = [
                f"## Baostock Income Statement — {ticker}",
                "",
                f"**Report Period**: {report_label}",
                "",
                "| Metric | Value |",
                "|--------|-------|",
            ]
            if revenue is not None:
                ilines.append(f"| Revenue | {_abs_big(revenue)} |")
            if net_profit is not None:
                ilines.append(f"| Net Profit | {_abs_big(net_profit)} |")
            ilines.append(f"| EPS (TTM) | {eps:.4f} 元" if eps is not None else "| EPS (TTM) | ? |")
            ilines.append(f"| Gross Margin | {_pct(gross_margin)} |")
            ilines.append(f"| Net Margin | {_pct(net_margin)} |")
            ilines.append(f"| Net Profit Growth (YoY) | {_pct(profit_growth)} |")
            ilines.append("")
            ilines.append("*(Data: Baostock)*")
            result["income_statement"] = "\n".join(ilines)

        # ── Cash flow (separate query, same session) ──
        year = int(curr_date[:4]) if curr_date and len(curr_date) >= 4 else datetime.now().year
        for try_year in (year, year - 1):
            for q in (4, 3, 2, 1):
                data = bs.query_cash_flow_data(code=code, year=try_year, quarter=q)
                row = _first_row(data, code)
                if row:
                    pub = str(row.get("pubDate", row.get("statDate", "?")))[:10]
                    clines = [
                        f"## Baostock Cash Flow — {ticker}",
                        "",
                        f"**Period**: {pub}",
                        "",
                        "*(Baostock cash flow fields are limited — see fundamentals table for key indicators.)*",
                    ]
                    result["cashflow"] = "\n".join(clines)
                    break
            if result["cashflow"]:
                break

    except Exception as exc:
        logger.warning("Baostock comprehensive fetch failed for %s: %s", ticker, exc)
    finally:
        _logout()

    with _bs_cache_lock:
        _bs_cache[cache_key] = result
    return result


# ── public API ────────────────────────────────────────────────────────────────


def get_fundamentals(
    ticker: str,
    curr_date: str = None,
) -> str:
    """Get key financial indicators for an A-share stock via Baostock.

    Returns a markdown table: ROE, margins, growth, EPS, balance ratios.
    Raises ``NoMarketDataError`` for non-CN tickers or when data is unavailable.
    """
    if not _is_cn_ticker(ticker):
        raise NoMarketDataError(ticker, ticker, "not a Chinese A-share ticker — baostock skipped")

    data = _fetch_all_bs_data(ticker, curr_date)
    result = data.get("fundamentals")
    if result is None:
        raise NoMarketDataError(ticker, ticker, "no profitability data from baostock")
    return result


def get_balance_sheet(
    ticker: str,
    freq: str = "quarterly",
    curr_date: str = None,
) -> str:
    """Get balance sheet summary via Baostock.

    Uses the per-run cache: same login session serves all 4 fundamental tools.
    """
    if not _is_cn_ticker(ticker):
        raise NoMarketDataError(ticker, ticker, "not a Chinese A-share ticker — baostock skipped")

    data = _fetch_all_bs_data(ticker, curr_date)
    result = data.get("balance_sheet")
    if result is None:
        raise NoMarketDataError(ticker, ticker, "no balance sheet data from baostock")
    return result


def get_cashflow(
    ticker: str,
    freq: str = "quarterly",
    curr_date: str = None,
) -> str:
    """Get cash flow summary via Baostock.

    Uses the per-run cache: same login session serves all 4 fundamental tools.
    """
    if not _is_cn_ticker(ticker):
        raise NoMarketDataError(ticker, ticker, "not a Chinese A-share ticker — baostock skipped")

    data = _fetch_all_bs_data(ticker, curr_date)
    result = data.get("cashflow")
    if result is None:
        raise NoMarketDataError(ticker, ticker, "no cash flow data from baostock")
    return result


def get_income_statement(
    ticker: str,
    freq: str = "quarterly",
    curr_date: str = None,
) -> str:
    """Get income statement summary via Baostock.

    Uses the per-run cache: same login session serves all 4 fundamental tools.
    """
    if not _is_cn_ticker(ticker):
        raise NoMarketDataError(ticker, ticker, "not a Chinese A-share ticker — baostock skipped")

    data = _fetch_all_bs_data(ticker, curr_date)
    result = data.get("income_statement")
    if result is None:
        raise NoMarketDataError(ticker, ticker, "no income statement data from baostock")
    return result
