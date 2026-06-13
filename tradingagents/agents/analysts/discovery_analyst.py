"""Discovery Analyst — proactive stock screening and ranking agent.

Given a screened list of candidate stocks (from ``cn_screener``) with
key financial metrics, the Discovery Analyst scores each candidate on
multiple dimensions and outputs a ranked watchlist with investment
rationale for the top picks.

Designed to fill the gap where the base TradingAgents system only
analyses pre-existing holdings — it never proactively suggests new
stocks to research.

Scoring dimensions (adapted from 3S-Trader framework):
1. **Financial Health** — ROE, debt ratio, current ratio, cash flow quality
2. **Growth Trajectory** — Revenue growth, earnings growth, margin trend
3. **Valuation Reasonableness** — PE/PB vs industry, vs growth rate (PEG)
4. **Business Quality** — Net margin, ROA, asset turnover (moat proxy)
5. **Market Interest** — Turnover rate, recent price momentum
6. **Theme Fit** — Relevance to the target industry/theme (if specified)
"""

from langchain_core.messages import HumanMessage, AIMessage

from tradingagents.agents.utils.agent_utils import get_language_instruction


def create_discovery_analyst(llm):
    """Create a Discovery Analyst node for proactive stock screening.

    The analyst receives a pre-screened candidate table and scores/ranks
    stocks on six dimensions. Output is a structured watchlist with
    rationale, suitable for saving to ``05 观察标的/``.
    """

    def discovery_node(screening_table: str, industry: str = "", top_k: int = 8) -> str:
        """Score and rank candidates from a pre-screened table.

        Args:
            screening_table: Markdown table from ``build_screening_table()``.
            industry: Target industry/theme name (for context).
            top_k: Number of top picks to detail in the output.

        Returns:
            A formatted markdown report with ranked picks and rationale.
        """
        theme_context = f"目标行业/主题: {industry}" if industry else "全市场筛选"

        prompt = f"""You are a Discovery Analyst — a senior investment researcher specialised in identifying high-conviction stock candidates from screened lists. Your job is to evaluate a batch of pre-filtered candidates and produce a ranked watchlist.

## Context
{theme_context}
筛选已排除: ST/*ST/退市风险股、市值 < 50亿、PE > 200 的标的。

## Candidate Table
{screening_table}

## Scoring Framework

Score each candidate on these six dimensions (1-10 scale):

1. **财务健康度 (Financial Health)**: ROE > 15% scores high; debt-to-asset < 40% scores high; current ratio > 1.5 scores high. Penalise excessive leverage or declining ROE.
2. **成长轨迹 (Growth Trajectory)**: Revenue growth > 20% scores high; accelerating growth gets bonus. Negative growth penalises.
3. **估值合理性 (Valuation Reasonableness)**: PE < industry median scores high; PEG < 1 scores high. PE > 50 without corresponding growth is a red flag.
4. **商业品质 (Business Quality)**: Net margin > 15% suggests moat; ROA > 8% suggests efficient capital allocation. Low margin + low turnover = commodity business.
5. **市场关注度 (Market Interest)**: Moderate turnover (2-5%) indicates healthy liquidity without overheating. Extreme turnover (>15%) may indicate speculation.
6. **主题契合度 (Theme Fit)**: If a specific industry/theme was given, how central is this company's business to that theme? Leader > participant > peripheral.

## Output Format

Return a markdown report structured as follows:

### 🏆 Top {top_k} Picks

For each top pick, provide:

**#{rank}. {stock_name} ({ticker}) — 综合评分: {score}/10**

| 维度 | 评分 | 要点 |
|------|------|------|
| 财务健康度 | X/10 | 关键指标 + 一句话判断 |
| 成长轨迹 | X/10 | 关键指标 + 一句话判断 |
| 估值合理性 | X/10 | 关键指标 + 一句话判断 |
| 商业品质 | X/10 | 关键指标 + 一句话判断 |
| 市场关注度 | X/10 | 关键指标 + 一句话判断 |
| 主题契合度 | X/10 | 与主题的关联判断 |

**投资逻辑**: (2-3 sentences explaining the core thesis)
**主要风险**: (1-2 sentences on what could go wrong)
**建议动作**: 深入研究 / 等待更好入场点 / 暂不考虑

### 📋 完整排名

A markdown table of all candidates sorted by composite score:
| 排名 | 代码 | 名称 | 综合评分 | 财务 | 成长 | 估值 | 品质 | 关注度 | 主题 |
|------|------|------|----------|------|------|------|------|--------|------|

### 🔍 发现洞察

2-4 sentences on patterns you noticed: industry concentration, overlooked gems, overheated segments, valuation anomalies, etc.

{get_language_instruction()}

**Important**: Base your analysis ONLY on the data provided in the candidate table. Do not fabricate metrics. If a metric is "?" (missing), note it as data-unavailable rather than guessing. Be skeptical — a high ROE with high debt deserves scrutiny, not automatic praise.
"""

        response = llm.invoke([HumanMessage(content=prompt)])
        return response.content

    return discovery_node
