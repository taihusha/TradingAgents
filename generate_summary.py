"""
生成持仓总览 — 一句话总结 + 链接到具体分析
"""
import re
from pathlib import Path

HOLDINGS = Path(r"E:\note\taihusha knowledge base\20 Areas\投资理财\03 持仓研究")
OUTPUT = HOLDINGS / "持仓卡片.md"

SKIP = ["_restructure_backup", "__pycache__"]

def extract_summary(content):
    """Extract dual-horizon ratings and one-line summary from a TA report."""
    # Look for primary rating
    rating_m = re.search(r'\*\*Rating\*\*:\s*(\S+)', content)
    rating_m2 = re.search(r'\*\*Recommendation\*\*:\s*(\S+)', content)
    rating = None
    if rating_m:
        rating = rating_m.group(1)
    elif rating_m2:
        rating = rating_m2.group(1)

    # Extract short-term and long-term ratings from the dual-horizon table.
    # Scope to the final-decision section (after **Rating**:) to avoid
    # matching unrelated tables elsewhere in the report (e.g. sentiment
    # analyst's data table). The 🏃/🏔️ emoji anchors uniquely identify
    # the dual-horizon rows.
    fd_start = content.find("**Rating**:")
    fd_section = content[fd_start:] if fd_start > 0 else content

    st_m = re.search(r'🏃.*?短线.*?\|\s*[🔴🟢🟡⚪]?\s*\**([A-Za-z]+)\**\s*\|', fd_section)
    lt_m = re.search(r'🏔.*?长线.*?\|\s*[🔴🟢🟡⚪]?\s*\**([A-Za-z]+)\**\s*\|', fd_section)
    st_rating = st_m.group(1) if st_m else None
    lt_rating = lt_m.group(1) if lt_m else None

    # Look for executive summary
    summary_m = re.search(r'\*\*Executive Summary\*\*:\s*(.+?)(?:\n\n|$)', content, re.DOTALL)
    if summary_m:
        summary = summary_m.group(1).strip()[:200]
    else:
        # Fallback: first non-heading paragraph after rating
        summary = "待分析"

    return rating, summary, st_rating, lt_rating


def main():
    stocks = []

    for d in sorted(HOLDINGS.iterdir()):
        if not d.is_dir() or d.name in SKIP or d.name.startswith("_"):
            continue
        if any(kw in d.name for kw in ["上证", "科创", "创AI", "富国"]):
            continue

        readme = d / "README.md"
        ticker = ""
        if readme.exists():
            m = re.search(r"ticker:\s*(\S+)", readme.read_text(encoding="utf-8"))
            if m:
                ticker = m.group(1)

        # Find latest TA report
        ta_files = sorted([f for f in d.glob("*.md") if f.name != "README.md"], reverse=True)
        rating, summary, st_rating, lt_rating = None, "待分析", None, None
        latest_date = ""

        if ta_files:
            latest = ta_files[0]
            latest_date = latest.stem
            content = latest.read_text(encoding="utf-8")
            rating, summary, st_rating, lt_rating = extract_summary(content)

        stocks.append({
            "name": d.name,
            "ticker": ticker,
            "latest_date": latest_date,
            "rating": rating,
            "st_rating": st_rating,
            "lt_rating": lt_rating,
            "summary": summary,
            "has_ta": len(ta_files) > 0,
        })

    # Generate markdown
    lines = []
    lines.append("# 持仓总览\n")
    lines.append(f"> 更新时间：自动生成 | 标的数量：{len(stocks)}\n")
    lines.append("| 标的 | Ticker | 最新分析 | 短线 (1-3月) | 长线 (6-18月) | 一句话总结 |")
    lines.append("|------|--------|----------|:------------:|:------------:|------------|")

    rating_emoji = {
        "Buy": "🟢", "Overweight": "🟢", "Strong Buy": "🟢",
        "Hold": "🟡", "Neutral": "🟡",
        "Underweight": "🔴", "Sell": "🔴", "Reduce": "🔴",
    }

    for s in stocks:
        name = s["name"]
        ticker = s["ticker"]
        st = s["st_rating"] or s["rating"] or "—"
        lt = s["lt_rating"] or s["rating"] or "—"
        st_emoji = rating_emoji.get(st, "⚪")
        lt_emoji = rating_emoji.get(lt, "⚪")

        if s["has_ta"] and s["latest_date"]:
            link = f"[{s['latest_date']}](./{name}/{s['latest_date']}.md)"
        else:
            link = "待分析"

        summary = s["summary"].replace("\n", " ").replace("|", "/")[:120]
        if not summary or summary == "待分析":
            summary = "—"

        # Highlight divergence between short-term and long-term
        st_cell = f"{st_emoji} {st}"
        lt_cell = f"{lt_emoji} {lt}"
        if st != lt and st != "—" and lt != "—":
            st_cell = f"**{st_cell}**"
            lt_cell = f"**{lt_cell}**"

        lines.append(f"| [{name}](./{name}/README.md) | {ticker} | {link} | {st_cell} | {lt_cell} | {summary} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("### 定时任务")
    lines.append("- **TradingAgents 周度持仓分析**：每周六 09:00 自动运行")
    lines.append("- 对全部 24 个标的运行完整 TradingAgents 分析流程")
    lines.append("- 结果保存为 `<标的>/<日期>.md`\n")

    output = "\n".join(lines)
    OUTPUT.write_text(output, encoding="utf-8")
    print(f"Written to {OUTPUT}")
    print(output)


if __name__ == "__main__":
    main()
