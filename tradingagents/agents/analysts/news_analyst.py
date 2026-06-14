from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_global_news,
    get_language_instruction,
    get_macro_indicators,
    get_news,
    get_prediction_markets,
)
from tradingagents.dataflows.cn_news import fetch_cn_news
from tradingagents.dataflows.config import get_config


def _is_cn_ticker(ticker: str) -> bool:
    return ticker.upper().endswith((".SZ", ".SS"))


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
        instrument_context = get_instrument_context_from_state(state)

        tools = [
            get_news,
            get_global_news,
            get_macro_indicators,
            get_prediction_markets,
        ]

        # Pre-fetch Chinese news for A-share stocks. Yahoo Finance /
        # Alpha Vantage have negligible coverage for Chinese tickers,
        # so the analyst would otherwise operate with "No news found"
        # and produce vacuous catalyst/risk signals.
        cn_news_block = ""
        cn_news_section = ""
        if _is_cn_ticker(ticker):
            cn_news_block = fetch_cn_news(ticker, current_date, current_date)
            if cn_news_block and cn_news_block.strip():
                cn_news_section = f"""### Chinese financial news — 东方财富个股新闻 & 宏观政策 (pre-fetched)
The following Chinese-language news has been pre-fetched for this A-share stock.
Western news sources (Yahoo Finance, Alpha Vantage) typically carry no coverage
for Chinese-listed equities, so this section is the PRIMARY news source for
catalyst identification. Analyse it thoroughly — do not default to saying
"no recent news" if Western sources return empty.

<start_of_cn_news>
{cn_news_block}
<end_of_cn_news>

"""

        system_message = (
            f"You are a news researcher tasked with analyzing recent news and trends over the past week. Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. Use the available tools: get_news(query, start_date, end_date) for {asset_label}-specific or targeted news searches, get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news, get_macro_indicators(indicator, curr_date, look_back_days) to ground macro commentary in actual data from FRED (e.g. 'cpi', 'core_pce', 'unemployment', 'fed_funds_rate', '10y_treasury', 'yield_curve'), and get_prediction_markets(topic, limit) for live market-implied probabilities of forward-looking events (e.g. 'Fed rate cut', 'recession 2026', geopolitical or sector events)."
            + f" For Chinese A-share stocks, company-specific and industry news is the PRIMARY conduit for identifying catalysts and risk factors — analyze it in depth. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + cn_news_section
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node
