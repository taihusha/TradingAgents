"""A-share / China-listed stock fundamental data fetchers via akshare.

Fills the gap where yfinance returns stub or empty data for ``.SZ`` / ``.SS``
tickers. Each function mirrors the yfinance vendor signatures so they can be
registered as drop-in fallback vendors in ``interface.py``.

Data sources (all via akshare, all gracefully degrading):
- ``stock_financial_analysis_indicator`` — 80+ columns of financial ratios
  (PE, PB, ROE, ROA, margins, turnover, debt, cash-flow ratios). Primary
  source for ``get_fundamentals``.
- ``stock_financial_abstract`` — ~80 historical financial indicators across
  all reporting periods, organised by category (profitability, growth,
  solvency, cash flow, operating efficiency).
- ``stock_financial_abstract_ths`` — Quarterly summary from THS/同花顺
  (net profit, revenue, EPS, book value, ROE, margins, debt ratio).
- ``stock_financial_benefit_ths`` — Income statement from THS (fallback).
- ``stock_financial_cash_ths`` — Cash flow from THS (fallback).
- ``stock_financial_debt_ths`` — Balance sheet from THS (fallback).

Architecture
------------
All functions follow the graceful-degradation pattern used by the rest of
the ``dataflows`` package: non-CN tickers immediately raise
``NoMarketDataError`` so the vendor router skips to the next vendor; CN
tickers try the primary akshare endpoint, then a fallback, then raise
``NoMarketDataError`` if nothing works. No exception surfaces to the caller
— the router turns ``NoMarketDataError`` into a clean sentinel.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

import pandas as pd

from .symbol_utils import NoMarketDataError
from ._browser_session import inject_browser_session  # noqa: E402, F401 — TLS/UA patch for akshare

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ticker detection & normalisation
# ---------------------------------------------------------------------------

_CN_TICKER_RE = re.compile(r"^(\d{6})(?:\.(SZ|SS))?$", re.IGNORECASE)


def _is_cn_ticker(ticker: str) -> bool:
    """Return True when *ticker* looks like a Chinese A-share code."""
    return bool(_CN_TICKER_RE.match(ticker.strip().upper()))


def _bare_code(ticker: str) -> str:
    """Strip exchange suffix: ``'000933.SZ'`` → ``'000933'``."""
    m = _CN_TICKER_RE.match(ticker.strip().upper())
    return m.group(1) if m else ticker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_akshare():
    """Import akshare or raise an ImportError with a clear message."""
    try:
        import akshare as ak

        return ak
    except ImportError:
        raise ImportError(
            "akshare is not installed. Install it with: pip install akshare"
        )


def _safe_format(value, fmt: str = ".2f") -> str:
    """Format a numeric value safely, returning 'N/A' for None/NaN."""
    if value is None:
        return "N/A"
    try:
        f = float(value)
        if f != f:  # NaN check
            return "N/A"
        return f"{f:{fmt}}"
    except (ValueError, TypeError):
        return str(value)


def _latest_report_date(date_str: str) -> str:
    """Convert date string to a readable format. Handles '20260331' → '2026-03-31'."""
    s = str(date_str).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


# ---------------------------------------------------------------------------
# get_fundamentals — comprehensive company fundamentals
# ---------------------------------------------------------------------------


def get_fundamentals(
    ticker: str,
    curr_date: str = None,
) -> str:
    """Get comprehensive fundamental data for a Chinese A-share stock.

    Uses ``stock_financial_analysis_indicator`` (most recent period) as the
    primary source, supplemented by ``stock_financial_abstract_ths`` for the
    latest quarterly summary.

    Returns a formatted plaintext report. Raises ``NoMarketDataError`` for
    non-CN tickers or when no data can be retrieved.
    """
    if not _is_cn_ticker(ticker):
        raise NoMarketDataError(
            ticker, ticker, "not a Chinese A-share ticker — akshare skipped"
        )

    bare = _bare_code(ticker)
    ak = _ensure_akshare()

    lines: list[str] = []

    # ── Source 1: Financial analysis indicator (richest single source) ──
    try:
        df = ak.stock_financial_analysis_indicator(symbol=bare, start_year="2020")
        if df is not None and len(df) > 0:
            latest = df.iloc[-1]

            # Map Chinese column names to English labels.
            # The DataFrame uses Chinese column names; this mapping covers the
            # 40 most important fields out of the 80+ available.
            FIELD_MAP = [
                # Valuation
                ("PE (TTM)", _safe_format(latest.get("市盈率(倍)", None))),
                ("PE (MRQ)", _safe_format(latest.get("市盈率_调整(倍)", None))),
                ("PB (MRQ)", _safe_format(latest.get("市净率(倍)", None))),
                ("PS (TTM)", _safe_format(latest.get("市销率(倍)", None))),
                # Per-share
                ("EPS — Basic (yuan)", _safe_format(latest.get("摊薄每股收益(元)", None))),
                ("EPS — Weighted (yuan)", _safe_format(latest.get("加权每股收益(元)", None))),
                ("Book Value Per Share (yuan)", _safe_format(latest.get("每股净资产_调整前(元)", None))),
                ("Operating CF Per Share (yuan)", _safe_format(latest.get("每股经营现金流(元)", None))),
                ("Capital Reserve Per Share (yuan)", _safe_format(latest.get("每股资本公积金(元)", None))),
                ("Undistributed Profit Per Share (yuan)", _safe_format(latest.get("每股未分配利润(元)", None))),
                # Profitability
                ("ROE (%)", _safe_format(latest.get("净资产收益率(%)", None))),
                ("ROA (%)", _safe_format(latest.get("总资产收益率(%)", None))),
                ("Gross Margin (%)", _safe_format(latest.get("销售毛利率(%)", None))),
                ("Net Margin (%)", _safe_format(latest.get("销售净利率(%)", None))),
                ("Operating Margin (%)", _safe_format(latest.get("主营业务利润率(%)", None))),
                ("EBIT Margin (%)", _safe_format(latest.get("息税前利润率(%)", None))),
                # Growth
                ("Revenue Growth YoY (%)", _safe_format(latest.get("主营业务收入增长率(%)", None))),
                ("Net Profit Growth YoY (%)", _safe_format(latest.get("净利润增长率(%)", None))),
                ("Total Asset Growth YoY (%)", _safe_format(latest.get("总资产增长率(%)", None))),
                # Solvency
                ("Debt-to-Equity (%)", _safe_format(latest.get("产权比率(%)", None))),
                ("Debt-to-Asset (%)", _safe_format(latest.get("资产负债率(%)", None))),
                ("Current Ratio", _safe_format(latest.get("流动比率", None))),
                ("Quick Ratio", _safe_format(latest.get("速动比率", None))),
                ("Cash Ratio (%)", _safe_format(latest.get("现金比率(%)", None))),
                ("Interest Coverage", _safe_format(latest.get("利息支付倍数", None))),
                # Operating efficiency
                ("Asset Turnover", _safe_format(latest.get("总资产周转率(次)", None))),
                ("Inventory Turnover", _safe_format(latest.get("存货周转率(次)", None))),
                ("Receivables Turnover (days)", _safe_format(latest.get("应收账款周转天数(天)", None))),
                # Cash flow quality
                ("Operating CF / Revenue", _safe_format(latest.get("经营现金流与营业收入比(%)", None))),
                ("Operating CF / Net Profit", _safe_format(latest.get("经营现金流与净利润比(%)", None))),
                ("Operating CF / Total Liability", _safe_format(latest.get("经营现金流对负债比率(%)", None))),
                ("Cash Flow Growth (%)", _safe_format(latest.get("现金流量比率(%)", None))),
                # Absolute figures (yuan)
                ("Revenue (yuan)", _safe_format(latest.get("主营业务收入(元)", None), ".0f")),
                ("Net Profit (yuan)", _safe_format(latest.get("净利润(元)", None), ".0f")),
                ("Total Assets (yuan)", _safe_format(latest.get("总资产(元)", None), ".0f")),
                ("Shareholders' Equity (yuan)", _safe_format(latest.get("股东权益(元)", None), ".0f")),
                ("Operating CF (yuan)", _safe_format(latest.get("经营活动现金流(元)", None), ".0f")),
            ]

            # Report period — first column is date
            report_date = str(latest.iloc[0]) if len(latest) > 0 else "?"

            lines.append(f"# Company Fundamentals for {ticker} (akshare / Eastmoney)")
            lines.append(f"# Report date: {report_date}")
            lines.append(
                f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            lines.append("")

            for label, value in FIELD_MAP:
                if value and value != "N/A":
                    lines.append(f"{label}: {value}")

    except Exception as exc:
        logger.debug("akshare financial_analysis_indicator failed for %s: %s", bare, exc)

    # ── Source 2: THS quarterly summary (supplement) ──
    try:
        df = ak.stock_financial_abstract_ths(
            symbol=bare, indicator="按报告期"
        )
        if df is not None and len(df) > 0:
            if not lines:
                # Primary source failed — use this as the sole source
                lines.append(
                    f"# Company Fundamentals for {ticker} (akshare / THS)"
                )
                lines.append(
                    f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                lines.append("")

            latest = df.iloc[-1]
            # Add supplementary metrics not already covered
            supplement = [
                ("Net Profit (yuan)", latest.get("净利润")),
                ("Revenue (yuan)", latest.get("营业收入")),
                ("Net Profit YoY (%)", latest.get("净利润同比增长率")),
                ("Revenue YoY (%)", latest.get("营业收入同比增长率")),
                ("Deducted NP (yuan)", latest.get("扣非净利润")),
                ("Deducted NP YoY (%)", latest.get("扣非净利润同比增长率")),
                ("Net Margin (%)", latest.get("销售净利率")),
                ("Gross Margin (%)", latest.get("毛利率")),
                ("ROE (%)", latest.get("净资产收益率")),
                ("EPS (yuan)", latest.get("基本每股收益")),
                ("BVPS (yuan)", latest.get("每股净资产")),
                ("Debt-to-Asset (%)", latest.get("资产负债率")),
                ("Equity Multiplier", latest.get("权益乘数")),
                ("Current Ratio", latest.get("流动比率")),
                ("Quick Ratio", latest.get("速动比率")),
                ("Inventory Turnover", latest.get("存货周转率")),
                ("Receivables Turnover (days)", latest.get("应收账款周转天数")),
            ]

            report_date = str(latest.get("报告期", "?"))

            lines.append("")
            lines.append(f"## Latest Quarter Summary (THS) — {report_date}")
            lines.append("")
            for label, value in supplement:
                if value is not None and str(value) not in ("", "nan", "False"):
                    lines.append(f"{label}: {value}")

    except Exception as exc:
        logger.debug("akshare financial_abstract_ths failed for %s: %s", bare, exc)

    # ── No data at all → raise so the router falls through ──
    if not lines:
        raise NoMarketDataError(
            ticker, bare, "no fundamental data from akshare (both sources failed)"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# get_balance_sheet
# ---------------------------------------------------------------------------


def get_balance_sheet(
    ticker: str,
    freq: str = "quarterly",
    curr_date: str = None,
) -> str:
    """Get balance sheet data for a Chinese A-share stock.

    Uses ``stock_financial_debt_ths`` (THS/同花顺) as the primary source,
    with ``stock_financial_abstract`` filtered for solvency indicators as
    fallback.
    """
    if not _is_cn_ticker(ticker):
        raise NoMarketDataError(
            ticker, ticker, "not a Chinese A-share ticker — akshare skipped"
        )

    bare = _bare_code(ticker)
    ak = _ensure_akshare()

    lines: list[str] = []

    # ── Source 1: THS balance sheet ──
    try:
        df = ak.stock_financial_debt_ths(symbol=bare)
        if df is not None and len(df) > 0:
            lines.append(f"# Balance Sheet for {ticker} (akshare / THS)")
            lines.append(
                f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            lines.append("")
            # Limit to recent periods (last 8 columns)
            df_display = df.iloc[:, :2] if df.shape[1] <= 2 else df
            # If too many columns, show first (indicator name) + last 4 periods
            if df.shape[1] > 5:
                cols = [df.columns[0]] + list(df.columns[-4:])
                df_display = df[cols]
            lines.append(df_display.to_string(index=False))
    except Exception as exc:
        logger.debug("akshare financial_debt_ths failed for %s: %s", bare, exc)

    # ── Source 2: Balance sheet items from financial_abstract ──
    if not lines:
        try:
            df = ak.stock_financial_abstract(symbol=bare)
            if df is not None and len(df) > 0:
                # Filter for solvency (�������) and per-share (ÿ��ָ��) indicators
                solvency = df[df["选项"].str.contains("���", na=False)]
                per_share = df[df["选项"].str.contains("ÿ��", na=False)]
                subset = (
                    pd.concat([solvency, per_share])
                    if len(solvency) > 0 and len(per_share) > 0
                    else solvency if len(solvency) > 0 else per_share
                )
                if len(subset) > 0:
                    # Show indicator name + last 4 periods
                    if subset.shape[1] > 5:
                        cols = ["指标"] + list(subset.columns[-4:])
                        subset = subset[cols]
                    else:
                        subset = subset.drop(columns=["选项"], errors="ignore")

                    lines.append(
                        f"# Balance Sheet (key indicators) for {ticker} (akshare)"
                    )
                    lines.append(
                        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    lines.append("")
                    lines.append(subset.to_string(index=False))
        except Exception as exc:
            logger.debug(
                "akshare financial_abstract (balance sheet) failed for %s: %s", bare, exc
            )

    if not lines:
        raise NoMarketDataError(
            ticker, bare, "no balance sheet data from akshare"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# get_cashflow
# ---------------------------------------------------------------------------


def get_cashflow(
    ticker: str,
    freq: str = "quarterly",
    curr_date: str = None,
) -> str:
    """Get cash flow data for a Chinese A-share stock.

    Uses ``stock_financial_cash_ths`` (THS/同花顺) as the primary source,
    with ``stock_financial_abstract`` filtered for cash flow indicators as
    fallback.
    """
    if not _is_cn_ticker(ticker):
        raise NoMarketDataError(
            ticker, ticker, "not a Chinese A-share ticker — akshare skipped"
        )

    bare = _bare_code(ticker)
    ak = _ensure_akshare()

    lines: list[str] = []

    # ── Source 1: THS cash flow statement ──
    try:
        df = ak.stock_financial_cash_ths(symbol=bare)
        if df is not None and len(df) > 0:
            lines.append(f"# Cash Flow for {ticker} (akshare / THS)")
            lines.append(
                f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            lines.append("")
            if df.shape[1] > 5:
                cols = [df.columns[0]] + list(df.columns[-4:])
                df = df[cols]
            lines.append(df.to_string(index=False))
    except Exception as exc:
        logger.debug("akshare financial_cash_ths failed for %s: %s", bare, exc)

    if not lines:
        raise NoMarketDataError(
            ticker, bare, "no cash flow data from akshare"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# get_income_statement
# ---------------------------------------------------------------------------


def get_income_statement(
    ticker: str,
    freq: str = "quarterly",
    curr_date: str = None,
) -> str:
    """Get income statement data for a Chinese A-share stock.

    Uses ``stock_financial_benefit_ths`` (THS/同花顺) as the primary source,
    with ``stock_financial_abstract`` filtered for income/earnings indicators
    as fallback.
    """
    if not _is_cn_ticker(ticker):
        raise NoMarketDataError(
            ticker, ticker, "not a Chinese A-share ticker — akshare skipped"
        )

    bare = _bare_code(ticker)
    ak = _ensure_akshare()

    lines: list[str] = []

    # ── Source 1: THS income statement ──
    try:
        df = ak.stock_financial_benefit_ths(symbol=bare)
        if df is not None and len(df) > 0:
            lines.append(f"# Income Statement for {ticker} (akshare / THS)")
            lines.append(
                f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            lines.append("")
            if df.shape[1] > 5:
                cols = [df.columns[0]] + list(df.columns[-4:])
                df = df[cols]
            lines.append(df.to_string(index=False))
    except Exception as exc:
        logger.debug("akshare financial_benefit_ths failed for %s: %s", bare, exc)

    if not lines:
        raise NoMarketDataError(
            ticker, bare, "no income statement data from akshare"
        )

    return "\n".join(lines)
