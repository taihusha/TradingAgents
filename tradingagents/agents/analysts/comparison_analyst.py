"""Cross-Stock Comparison Analyst — relative ranking within an industry.

After individual analyses complete for all holdings, the Comparison
Analyst reads the reports grouped by industry and produces a relative
ranking. This fills the gap where stocks are currently analysed in
isolation — the system knows each stock's absolute rating but cannot
answer "which is the better bet in automotive parts?"

Input: Multiple TradingAgents analysis reports (the final decision
sections plus key metrics), grouped by industry/theme.

Output: A ranked comparison with relative-strength analysis across
dimensions: value-chain position, financial quality, valuation,
growth, risk, and catalyst timing.
"""

from langchain_core.messages import HumanMessage

from tradingagents.agents.utils.agent_utils import get_language_instruction


def create_comparison_analyst(llm):
    """Create a Comparison Analyst for cross-stock ranking.

    Call with a list of ``(ticker, folder_name, final_decision_text)``
    tuples and an optional industry label. Returns a formatted markdown
    comparison report.
    """

    def compare_stocks(
        stocks: list[tuple[str, str, str]],  # (ticker, name, decision_text)
        industry: str = "",
    ) -> str:
        """Compare multiple stocks and produce a ranked report.

        Args:
            stocks: List of (ticker, folder_name, final_decision) tuples.
            industry: Optional industry label for context.

        Returns:
            Markdown comparison report.
        """
        if len(stocks) < 2:
            return ""

        header = f"## 跨标的比较分析 — {industry}" if industry else "## 跨标的比较分析"

        # Build the analysis blocks for each stock
        stock_blocks = []
        for ticker, name, decision in stocks:
            # Extract key sections to keep prompt size manageable
            # Take last 2000 chars of the decision (contains the rating + rationale)
            excerpt = decision[-2500:] if len(decision) > 2500 else decision
            stock_blocks.append(
                f"### {name} ({ticker})\n\n{excerpt}\n\n---\n"
            )

        combined = "\n".join(stock_blocks)

        prompt = f"""You are a Cross-Stock Comparison Analyst — a senior portfolio strategist who evaluates multiple stocks within the same sector and determines their relative ranking. Your analysis directly informs position sizing and capital allocation decisions.

{header}

## Analysis Reports

Below are the individual TradingAgents analysis reports for {len(stocks)} stocks. Each includes the Market Analyst, Sentiment Analyst, News Analyst, Fundamentals Analyst, Bull/Bear Debate, Risk Debate, and Final Decision sections.

{combined}

## Comparison Task

Compare these stocks across the following dimensions and produce a relative ranking:

### 1. 产业链位置 (Value-Chain Position)
- Which company sits closest to a scarce/constrained layer?
- Which has the strongest pricing power or customer lock-in?
- Which is most vulnerable to substitution or disintermediation?

### 2. 财务质量 (Financial Quality)
- Compare ROE, margins, debt levels, cash flow quality
- Which has the most sustainable earnings power?
- Which has hidden balance-sheet risks?

### 3. 估值吸引力 (Valuation)
- Compare PE/PB relative to each other and to growth rates
- Which offers the best risk/reward at current prices?
- Which is priced for perfection (most downside risk on a miss)?

### 4. 成长前景 (Growth Outlook)
- Compare revenue/earnings growth trajectories
- Which has the strongest near-term catalyst pipeline?
- Which has the most credible long-term growth narrative?

### 5. 风险调整后吸引力 (Risk-Adjusted Appeal)
- Weighing all factors: growth, quality, valuation, risk
- Which stock would you size LARGEST in a portfolio and why?
- Which would you size smallest or avoid?

## Output Format

{header}

### 📊 多维度对比矩阵

| 维度 | {' | '.join(name for _, name, _ in stocks)} |
|------|{'|'.join('------' for _ in stocks)}|
| 产业链位置 | ... |
| 财务质量 | ... |
| 估值吸引力 | ... |
| 成长前景 | ... |
| 风险水平 | ... |

*每格填入: 排名 (1-{len(stocks)}) + 一句话理由*

### 🏆 综合排名

**#1. {{name}} ({{ticker}})** — 核心理由 (2 sentences)
**#2. {{name}} ({{ticker}})** — 核心理由 (2 sentences)
...

### 💡 组合建议

2-3 sentences on how these stocks complement or overlap in a portfolio context. If two are highly correlated or serve the same exposure, flag that. If one is a natural hedge for another's risk, note it.

{get_language_instruction()}

**Important**: Be decisive. Every dimension must have a clear ranking — no ties. Use the SHORT-TERM and LONG-TERM ratings from each stock's Final Decision as tiebreakers if needed. A stock rated Long-Term Buy beats one rated Long-Term Hold, all else equal.
"""

        response = llm.invoke([HumanMessage(content=prompt)])
        return response.content

    return compare_stocks
