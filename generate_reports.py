"""
从 TradingAgents JSON 日志生成完整的 Markdown 分析报告。
用法: PYTHONIOENCODING=utf-8 python generate_reports.py

读取 ~/.tradingagents/logs/<TICKER>/TradingAgentsStrategy_logs/full_states_log_2026-06-13.json
生成完整报告（含四大分析报告 + 牛熊辩论 + 风险辩论 + 最终决策）
"""
import json
import os
from pathlib import Path

# --- 配置 ---
TRADE_DATE = "2026-06-13"
LOGS_DIR = os.path.join(os.path.expanduser("~"), ".tradingagents", "logs")
OUTPUT_DIR = r"E:\note\taihusha knowledge base\20 Areas\投资理财\03 持仓研究"

# 股票代码 -> 公司中文名 映射
COMPANY_NAMES = {
    "000933.SZ": "神火股份",
    "600378.SS": "昊华科技",
    "002472.SZ": "双环传动",
    "002491.SZ": "通鼎互联",
    "002549.SZ": "002549.SZ",
    "600111.SS": "北方稀土",
    "000725.SZ": "京东方A",
    "600367.SS": "红星发展",
    "600397.SS": "江钨装备",
    "002185.SZ": "华天科技",
    "603773.SS": "沃格光电",
    "600522.SS": "中天科技",
    "600487.SS": "亨通光电",
    "NOW": "NOW",
    "MRVL": "MRVL",
    "NOK": "NOK",
    "IREN": "IREN",
    "DRAM": "DRAM",
    "TEAM": "TEAM",
    "PATH": "PATH",
}

# 文件名映射
FILE_MAP = {
    "000933.SZ": "神火股份_2026-06-13.md",
    "600378.SS": "昊华科技_2026-06-13.md",
    "002472.SZ": "双环传动_2026-06-13.md",
    "002491.SZ": "通鼎互联.md",
    "002549.SZ": "002549.SZ_TradingAgents分析_2026-06-13.md",
    "600111.SS": "北方稀土.md",
    "000725.SZ": "京东方A.md",
    "600367.SS": "红星发展.md",
    "600397.SS": "江钨装备_2026-06-13.md",
    "002185.SZ": "华天科技.md",
    "603773.SS": "沃格光电_2026-06-13.md",
    "600522.SS": "中天科技_2026-06-13.md",
    "600487.SS": "亨通光电_2026-06-13.md",
    "NOW": "NOW.md",
    "MRVL": "MRVL.md",
    "NOK": "NOK.md",
    "IREN": "IREN.md",
    "DRAM": "DRAM.md",
    "TEAM": "TEAM.md",
    "PATH": "PATH.md",
}


def format_debate_history(history_list, role_name):
    """Format a debate history as readable markdown.

    The history may be:
    - A list of single-character strings (LangGraph streaming tokens) → join into one text
    - A list of message dicts or long strings → format each separately
    """
    if not history_list:
        return f"（{role_name}未发表观点）\n\n"

    # Detect token-level streaming: all items are strings of length 1
    # (or length 0-2 for newlines etc.)
    is_streaming = all(
        isinstance(x, str) and len(x) <= 2 for x in history_list
    )

    if is_streaming:
        # Concatenate all tokens into the full text
        full_text = "".join(history_list).strip()

        # Split into rounds by "Analyst:" markers
        # Each round starts with something like "Bull Analyst:" or "Bear Analyst:"
        import re
        parts = re.split(r'(\n?(?:Bull|Bear|Aggressive|Conservative|Neutral)\s+Analyst:)', full_text)

        if len(parts) <= 1:
            # No clear round markers — just return the full text
            return f"#### {role_name}\n\n{full_text}\n\n"

        # Reassemble: parts[0] is before first marker (usually empty),
        # then alternating (marker, content) pairs
        rounds = []
        i = 1  # skip leading empty
        round_num = 0
        while i < len(parts) - 1:
            marker = parts[i].strip()
            content = parts[i + 1].strip()
            round_num += 1
            rounds.append(f"#### {role_name} 第 {round_num} 轮\n\n{content}\n")
            i += 2

        return "\n".join(rounds) if rounds else f"#### {role_name}\n\n{full_text}\n\n"

    else:
        # Original message-based format (dicts or long strings)
        lines = []
        for i, msg in enumerate(history_list, 1):
            if isinstance(msg, dict):
                content = msg.get("content", str(msg))
            else:
                content = str(msg)

            if content.startswith("SystemMessage") or content.startswith("ToolMessage"):
                continue
            if not content.strip():
                continue

            if len(content) > 4000:
                content = content[:4000] + "\n\n...（内容过长，已截断）"

            lines.append(f"#### {role_name} 第 {i} 轮\n\n{content}\n")

        return "\n".join(lines) if lines else f"（{role_name}无有效内容）\n\n"


def load_json_log(ticker):
    """Load the JSON log for a ticker."""
    json_path = os.path.join(
        LOGS_DIR, ticker, "TradingAgentsStrategy_logs",
        f"full_states_log_{TRADE_DATE}.json"
    )
    if not os.path.exists(json_path):
        return None
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_full_report(ticker, data):
    """Build a complete markdown report from the JSON data."""
    company = COMPANY_NAMES.get(ticker, ticker)

    report = []

    report.append(f"# {ticker} {company} — TradingAgents 完整分析报告\n")
    report.append(f"**日期**: {TRADE_DATE} | **模型**: DeepSeek v4-pro / v4-flash | **研究深度**: 2\n")
    report.append("---\n")

    # 章节列表
    sections = [
        ("市场 / 技术面分析（Market Analyst）", "market_report"),
        ("情绪面分析（Sentiment Analyst）", "sentiment_report"),
        ("新闻与宏观分析（News Analyst）", "news_report"),
        ("基本面分析（Fundamentals Analyst）", "fundamentals_report"),
    ]

    for i, (title, key) in enumerate(sections, 1):
        report.append(f"## {chr(9311 + i)} {title}\n")
        content = data.get(key, "")
        report.append(content if content else "（无数据）")
        report.append("\n---\n")

    # 牛熊辩论
    report.append("## 五、牛熊辩论（Bull vs Bear Debate）\n")
    debate = data.get("investment_debate_state", {})
    bull = debate.get("bull_history", [])
    bear = debate.get("bear_history", [])
    if bull or bear:
        report.append(f"### 🐂 多头方（共 {len(bull)} 条消息）\n")
        report.append(format_debate_history(bull, "多头"))
        report.append(f"### 🐻 空头方（共 {len(bear)} 条消息）\n")
        report.append(format_debate_history(bear, "空头"))
    else:
        report.append("（无辩论数据）\n")
    judge = debate.get("judge_decision", "")
    if judge:
        report.append("### ⚖ 辩论裁判裁决\n")
        report.append(judge)
        report.append("\n")
    report.append("---\n")

    # 交易员决策
    report.append("## 六、交易员投资决策（Trader Investment Decision）\n")
    trader = data.get("trader_investment_decision", "")
    report.append(trader if trader else "（无数据）")
    report.append("\n---\n")

    # 风险管理辩论
    report.append("## 七、风险管理辩论（Risk Management Debate）\n")
    risk = data.get("risk_debate_state", {})
    for role, key, emoji in [
        ("激进派", "aggressive_history", "🔥"),
        ("保守派", "conservative_history", "🛡"),
        ("中性派", "neutral_history", "⚖"),
    ]:
        hist = risk.get(key, [])
        report.append(f"### {emoji} {role}（共 {len(hist)} 条消息）\n")
        report.append(format_debate_history(hist, role))
    risk_judge = risk.get("judge_decision", "")
    if risk_judge:
        report.append("### ⚖ 风险裁判裁决\n")
        report.append(risk_judge)
        report.append("\n")
    report.append("---\n")

    # 最终决策
    report.append("## 八、最终投资计划（Investment Plan）\n")
    plan = data.get("investment_plan", "")
    report.append(plan if plan else "（无数据）")
    report.append("\n---\n")

    report.append("## 九、最终交易决策（Final Trade Decision）\n")
    final = data.get("final_trade_decision", "")
    report.append(final if final else "（无数据）")
    report.append("")

    return "\n".join(report)


def save_report(filename, report):
    """Save report: replaces any previous TradingAgents section, otherwise append/create."""
    filepath = os.path.join(OUTPUT_DIR, filename)

    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            existing = f.read()

        # Find and strip any previous TradingAgents analysis section.
        # Look for either the brief header "## TradingAgents 分析" or the
        # full report header "# <TICKER> ... TradingAgents 完整分析报告"
        import re
        # Match the separator + full report header, or the brief header
        m = re.search(
            r'(\n---\n\n# .*?TradingAgents 完整分析报告|## TradingAgents 分析)',
            existing
        )
        if m:
            existing = existing[:m.start()].rstrip()

        # Append full report
        new_content = existing.rstrip() + "\n\n---\n\n" + report
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)
        return "updated"
    else:
        # New file
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(report)
        return "created"


def main():
    print("=" * 60)
    print("TradingAgents 完整报告生成器")
    print(f"  日期: {TRADE_DATE}")
    print(f"  日志目录: {LOGS_DIR}")
    print(f"  输出目录: {OUTPUT_DIR}")
    print("=" * 60)
    print()

    created, updated, skipped = 0, 0, 0

    for ticker in sorted(COMPANY_NAMES.keys()):
        print(f"📊 {ticker} ... ", end="", flush=True)

        data = load_json_log(ticker)
        if data is None:
            print("⚠ JSON 日志不存在，跳过")
            skipped += 1
            continue

        try:
            report = build_full_report(ticker, data)
        except Exception as e:
            print(f"❌ 生成报告失败: {e}")
            skipped += 1
            continue

        filename = FILE_MAP.get(ticker, f"{ticker}_TradingAgents分析_{TRADE_DATE}.md")
        result = save_report(filename, report)

        if result == "skip":
            print("⏭ 已存在完整报告")
            skipped += 1
        elif result == "created":
            print("✅ 新创建")
            created += 1
        elif result == "updated":
            print("✅ 已更新（替换旧简要版）")
            updated += 1

    print()
    print("=" * 60)
    print(f"完成: {created} 新建, {updated} 更新, {skipped} 跳过")
    print("=" * 60)


if __name__ == "__main__":
    main()
