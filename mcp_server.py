"""
TradingAgents MCP Server — expose stock analysis as Claude-callable tools.

Run as a stdio MCP server so Claude Code / Claude Desktop can invoke
TradingAgents directly from conversation.

Configuration (Claude Code .claude/mcp.json):
{
  "mcpServers": {
    "tradingagents": {
      "command": ".venv/Scripts/python",
      "args": ["mcp_server.py"],
      "cwd": "E:/codex-workspace/projects/TradingAgents"
    }
  }
}

Tools exposed:
  analyze_stock   — Full multi-agent analysis for a single ticker
  discover_stocks — Industry/theme-based screening + LLM ranking
  list_holdings   — List current holdings from filesystem
  get_report      — Read latest saved analysis report
  compare_stocks  — Cross-stock comparison within an industry
  get_token_costs — Token usage & cost summary
"""

import os
import sys
from datetime import date
from pathlib import Path

# Ensure project is on path
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "TradingAgents",
    instructions="Multi-agent stock analysis framework with A-share support. "
    "Provides full fundamental/technical/sentiment/news analysis, "
    "bull/bear debate, risk management, and dual-horizon investment "
    "decisions for Chinese A-shares, US, and HK stocks.",
)

HOLDINGS_DIR = Path(r"E:\note\taihusha knowledge base\20 Areas\投资理财\03 持仓研究")


# ── Helpers ────────────────────────────────────────────────────────────────


def _get_llm(quick: bool = False):
    """Create an LLM instance from configured defaults."""
    from tradingagents.llm_clients.factory import create_llm_client
    from tradingagents.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()
    model = config["quick_think_llm"] if quick else config["deep_think_llm"]
    client = create_llm_client(
        provider=config["llm_provider"],
        model=model,
        base_url=config.get("backend_url"),
    )
    return client.get_llm()


def _discover_ticker_map() -> dict[str, str]:
    """Scan holdings directory, return {folder_name: ticker}."""
    import re
    ticker_map = {}
    if not HOLDINGS_DIR.is_dir():
        return ticker_map
    for d in sorted(HOLDINGS_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        readme = d / "README.md"
        if readme.exists():
            text = readme.read_text(encoding="utf-8")
            m = re.search(r"ticker:\s*(\S+)", text)
            if m:
                ticker_map[d.name] = m.group(1).strip()
    return ticker_map


# ── MCP Tools ──────────────────────────────────────────────────────────────


@mcp.tool()
async def analyze_stock(
    ticker: str,
    date_str: str = "",
    debate_rounds: int = 2,
) -> str:
    """Run full multi-agent stock analysis on a single ticker.

    Executes the complete TradingAgents pipeline: Market Analyst,
    Sentiment Analyst, News Analyst, Fundamentals Analyst, Bull/Bear
    Debate, Risk Debate, and Portfolio Manager final decision with
    dual-horizon (short-term + long-term) rating.

    Args:
        ticker: Ticker symbol with exchange suffix (e.g. '000933.SZ', 'AAPL')
        date_str: Analysis date YYYY-MM-DD (default: today)
        debate_rounds: Max debate rounds (1-3, default 2)

    Returns:
        Markdown analysis report with final investment decision
    """
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.agents.utils.agent_utils import load_supply_chain_context
    from tradingagents.utils.token_tracker import TokenTracker

    analysis_date = date_str or date.today().strftime("%Y-%m-%d")
    tracker = TokenTracker()

    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = max(1, min(debate_rounds, 3))
    config["max_risk_discuss_rounds"] = max(1, min(debate_rounds, 3))
    config["checkpoint_enabled"] = False  # disable for MCP; faster

    # Try to find supply-chain context
    ticker_map = _discover_ticker_map()
    folder_name = None
    for name, t in ticker_map.items():
        if t.upper() == ticker.upper():
            folder_name = name
            break
    supply_chain_ctx = load_supply_chain_context(str(HOLDINGS_DIR), folder_name) if folder_name else ""

    # Short name for output
    display_name = folder_name or ticker

    ta = TradingAgentsGraph(debug=False, config=config, callbacks=[tracker])
    final_state, decision = ta.propagate(
        ticker, analysis_date, supply_chain_context=supply_chain_ctx
    )

    # Build a concise report
    rating = ""
    if isinstance(decision, dict):
        rating = decision.get("rating", "")
    elif isinstance(decision, str):
        rating = decision[:80]

    lines = [
        f"# {ticker} {display_name} — TradingAgents Analysis",
        f"**Date**: {analysis_date} | **Rating**: {rating}",
        "",
        "---",
        "",
        final_state.get("fundamentals_report", ""),
        "",
        "---",
        "",
        "## Final Decision",
        "",
        final_state.get("final_trade_decision", ""),
        "",
        "---",
        "",
        tracker.one_line(),
    ]
    return "\n".join(lines)


@mcp.tool()
async def discover_stocks(
    industry: str = "",
    concept: str = "",
    min_market_cap: float = 50,
    top_k: int = 8,
) -> str:
    """Screen A-shares by industry or theme and rank candidates.

    Uses akshare for data + LLM for 6-dimension scoring (financial
    health, growth, valuation, business quality, market interest,
    theme fit). Outputs a ranked watchlist with investment rationale.

    Args:
        industry: Eastmoney industry name (e.g. '汽车零部件', '半导体')
        concept: Concept board name (e.g. '机器人概念', 'CPO概念')
        min_market_cap: Minimum market cap in 亿 yuan (default 50)
        top_k: Number of top picks to detail (default 8)

    Returns:
        Ranked watchlist in markdown with per-stock scoring and rationale
    """
    from tradingagents.dataflows.cn_screener import (
        screen_stocks,
        get_financial_snapshots,
        build_screening_table,
        get_industry_list,
    )
    from tradingagents.agents.analysts.discovery_analyst import create_discovery_analyst

    if not industry and not concept:
        return (
            "Please specify --industry or --concept.\n\n"
            "Use list_holdings tool to see what industries your holdings are in, "
            "or use industry names like: 汽车零部件, 半导体, 光伏设备, 电池, "
            "白酒, 医疗器械, 自动化设备, 消费电子, etc.\n\n"
            "Tip: You can also ask me to list available industries."
        )

    label = industry or concept
    min_mc_yuan = min_market_cap * 1e8

    # Screen
    stocks = screen_stocks(
        industry=industry or None,
        concept=concept or None,
        min_market_cap=min_mc_yuan,
    )

    if not stocks:
        return f"No stocks found for '{label}' (min market cap {min_market_cap}亿). The Eastmoney API may be unreachable from outside mainland China."

    # Financial snapshots for top candidates
    top_candidates = stocks[:50]
    tickers = [s["ticker"] for s in top_candidates]
    financials = get_financial_snapshots(tickers, max_stocks=50)

    # Build table and run LLM
    table = build_screening_table(stocks, financials, top_n=40)
    llm = _get_llm(quick=False)
    discovery = create_discovery_analyst(llm)
    report = discovery(table, industry=label, top_k=top_k)

    header = (
        f"# Stock Discovery — {label}\n"
        f"**Date**: {date.today().strftime('%Y-%m-%d')}\n"
        f"**Screened**: {len(stocks)} candidates → {len(financials)} with financials\n\n"
    )
    return header + report


@mcp.tool()
async def list_holdings() -> str:
    """List all current holdings with tickers and industries.

    Scans the holdings directory and returns a table of all tracked
    stocks, useful for knowing what's in the portfolio before
    running analysis or discovery.

    Returns:
        Markdown table of holdings with ticker, name, industry
    """
    ticker_map = _discover_ticker_map()
    if not ticker_map:
        return "No holdings found."

    lines = [
        f"## Current Holdings ({len(ticker_map)} stocks)",
        "",
        "| # | Name | Ticker |",
        "|---|------|--------|",
    ]
    for i, (name, ticker) in enumerate(sorted(ticker_map.items()), 1):
        lines.append(f"| {i} | {name} | {ticker} |")

    return "\n".join(lines)


@mcp.tool()
async def get_report(ticker: str, date_str: str = "") -> str:
    """Read the latest saved analysis report for a stock.

    Looks up the holding folder for *ticker* and returns the most
    recent dated analysis report. Use this to review past analysis
    without re-running the full pipeline.

    Args:
        ticker: Ticker symbol (e.g. '000933.SZ', 'NVDA')
        date_str: Specific date YYYY-MM-DD, or empty for latest

    Returns:
        The full markdown analysis report, or an error message
    """
    ticker_map = _discover_ticker_map()
    folder_name = None
    for name, t in ticker_map.items():
        if t.upper() == ticker.upper():
            folder_name = name
            break

    if not folder_name:
        return f"Ticker '{ticker}' not found in holdings. Use list_holdings to see available stocks."

    folder = HOLDINGS_DIR / folder_name
    if not folder.is_dir():
        return f"Folder not found: {folder}"

    # Find reports (*.md files that aren't README.md)
    reports = sorted(
        [f for f in folder.glob("*.md") if f.name != "README.md"],
        reverse=True,
    )

    if date_str:
        reports = [r for r in reports if r.stem == date_str]

    if not reports:
        return f"No analysis reports found for {ticker} ({folder_name})."

    report_path = reports[0]
    content = report_path.read_text(encoding="utf-8")
    return f"## Report: {folder_name} ({ticker}) — {report_path.stem}\n\n{content}"


@mcp.tool()
async def compare_stocks(tickers: str) -> str:
    """Run cross-stock comparison for a set of tickers.

    Reads each stock's latest analysis report and produces a ranked
    comparison across 5 dimensions: value-chain position, financial
    quality, valuation, growth outlook, and risk-adjusted appeal.

    Args:
        tickers: Comma-separated ticker symbols (e.g. '000933.SZ,002472.SZ,600111.SS')
                 Use 'all' to compare all holdings grouped by industry.

    Returns:
        Markdown comparison report with ranking matrix
    """
    from tradingagents.agents.analysts.comparison_analyst import create_comparison_analyst

    ticker_map = _discover_ticker_map()

    if tickers.strip().lower() == "all":
        t_list = list(ticker_map.values())
    else:
        t_list = [t.strip() for t in tickers.split(",") if t.strip()]

    if len(t_list) < 2:
        return "Need at least 2 tickers to compare. Use 'all' to compare all holdings."

    # Gather latest reports
    stock_data: list[tuple[str, str, str]] = []  # (ticker, name, decision_text)
    for ticker in t_list:
        folder_name = None
        for name, t in ticker_map.items():
            if t.upper() == ticker.upper():
                folder_name = name
                break
        if not folder_name:
            continue

        folder = HOLDINGS_DIR / folder_name
        reports = sorted(
            [f for f in folder.glob("*.md") if f.name != "README.md"],
            reverse=True,
        )
        if reports:
            content = reports[0].read_text(encoding="utf-8")
            # Extract final decision section
            decision_start = content.find("## 七、最终投资决策")
            if decision_start == -1:
                decision_start = content.find("## Final Decision")
            if decision_start >= 0:
                decision_text = content[decision_start:]
            else:
                decision_text = content[-3000:]
            stock_data.append((ticker, folder_name, decision_text))

    if len(stock_data) < 2:
        return f"Could not find reports for enough tickers. Found: {len(stock_data)}"

    llm = _get_llm(quick=True)
    compare = create_comparison_analyst(llm)
    report = compare(stock_data)

    ticker_list = ", ".join(t for t, _, _ in stock_data)
    return f"# Cross-Stock Comparison — {ticker_list}\n\n{report}"


@mcp.tool()
async def get_token_costs() -> str:
    """Return current DeepSeek API pricing reference.

    Token cost tracking is per-session (tracked during analyze_stock
    and weekly analysis runs). This tool returns the pricing reference
    table. Actual session costs are returned inline after each
    analyze_stock call.

    Returns:
        Pricing table for supported models
    """
    from tradingagents.utils.token_tracker import MODEL_PRICING

    lines = [
        "## LLM API Pricing Reference (per 1M tokens)",
        "",
        "| Model | Input ($) | Output ($) |",
        "|-------|-----------|------------|",
    ]
    for model, (in_price, out_price) in sorted(MODEL_PRICING.items()):
        lines.append(f"| {model} | {in_price:.2f} | {out_price:.2f} |")

    lines.extend([
        "",
        "Token costs are tracked per-session during analyze_stock calls.",
        "The cost summary appears at the end of each analysis report.",
    ])
    return "\n".join(lines)


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
