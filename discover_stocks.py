"""
主动选股工具 — 基于行业/主题扫描市场，发现值得深入研究的标的。

用法:
    # 按行业筛选
    .venv/Scripts/python discover_stocks.py --industry "汽车零部件"

    # 按概念板块筛选
    .venv/Scripts/python discover_stocks.py --concept "机器人概念"

    # 全市场筛选（按市值前200 + 财务过滤）
    .venv/Scripts/python discover_stocks.py --all --min-market-cap 100

    # 列出所有可用行业
    .venv/Scripts/python discover_stocks.py --list-industries
"""
import argparse
import os
import sys
from datetime import date

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tradingagents.dataflows.cn_screener import (
    get_industry_list,
    screen_stocks,
    get_financial_snapshots,
    build_screening_table,
)
from tradingagents.agents.analysts.discovery_analyst import create_discovery_analyst
from tradingagents.llm_clients.factory import create_llm_client
from tradingagents.default_config import DEFAULT_CONFIG


def main():
    parser = argparse.ArgumentParser(description="主动选股 — 行业/主题扫描与候选排序")
    parser.add_argument("--industry", type=str, help="行业名称（如 '汽车零部件'）")
    parser.add_argument("--concept", type=str, help="概念板块名称（如 '机器人概念'）")
    parser.add_argument("--all", action="store_true", help="全市场筛选")
    parser.add_argument("--min-market-cap", type=float, default=50,
                        help="最小市值（亿元），默认 50")
    parser.add_argument("--top-k", type=int, default=8,
                        help="最终推荐数量，默认 8")
    parser.add_argument("--list-industries", action="store_true",
                        help="列出所有可用行业名称")
    parser.add_argument("--output", type=str, default="",
                        help="输出文件路径（可选）")
    args = parser.parse_args()

    # ── List industries mode ──
    if args.list_industries:
        print("正在获取行业列表...")
        industries = get_industry_list()
        if not industries:
            print("❌ 无法获取行业列表")
            return 1
        print(f"共 {len(industries)} 个行业:\n")
        for i, ind in enumerate(industries, 1):
            print(f"  {i:3d}. {ind['name']:20s} ({ind['code']})")
        return 0

    # ── Validate inputs ──
    if not args.industry and not args.concept and not args.all:
        parser.print_help()
        print("\n提示: 使用 --list-industries 查看可用行业")
        return 1

    # ── Screen stocks ──
    min_mc = args.min_market_cap * 1e8  # Convert 亿 to 元
    label = args.industry or args.concept or "全市场"

    print(f"🔍 正在筛选: {label}（最小市值 {args.min_market_cap} 亿）...")

    try:
        stocks = screen_stocks(
            industry=args.industry,
            concept=args.concept,
            min_market_cap=min_mc,
        )
    except Exception as e:
        print(f"❌ 筛选失败: {e}")
        return 1

    if not stocks:
        print(f"❌ 未找到符合条件的标的（{label}）")
        return 1

    print(f"✅ 初筛通过: {len(stocks)} 只标的")

    # ── Get financial snapshots for top candidates ──
    top_candidates = stocks[:50]  # financial snapshots are slow, limit to top 50
    print(f"📊 正在获取财务指标（前 {len(top_candidates)} 只）...")
    tickers = [s["ticker"] for s in top_candidates]
    financials = get_financial_snapshots(tickers, max_stocks=50)
    print(f"✅ 财务数据获取: {len(financials)} 只")

    # ── Build screening table ──
    table = build_screening_table(stocks, financials, top_n=40)
    print(f"\n{table[:500]}...\n")

    # ── LLM Discovery Analyst ──
    print("🤖 正在调用 Discovery Analyst 进行评分排序...")
    config = DEFAULT_CONFIG.copy()

    client = create_llm_client(
        provider=config["llm_provider"],
        model=config["deep_think_llm"],
        base_url=config.get("backend_url"),
    )
    llm = client.get_llm()

    discovery = create_discovery_analyst(llm)
    report = discovery(table, industry=label, top_k=args.top_k)

    print("\n" + "=" * 60)
    print(report)
    print("=" * 60)

    # ── Save output ──
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(f"# 主动选股报告 — {label}\n")
            f.write(f"**日期**: {date.today().strftime('%Y-%m-%d')}\n\n")
            f.write(report)
        print(f"\n✅ 报告已保存: {args.output}")
    else:
        # Default: save to observations directory
        obs_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "..", "taihusha knowledge base",
            "20 Areas", "投资理财", "05 观察标的",
        )
        obs_dir = os.path.normpath(obs_dir)
        safe_label = label.replace("/", "-").replace("\\", "-")
        filename = f"{date.today().strftime('%Y%m%d')}_{safe_label}_筛选.md"
        if os.path.isdir(obs_dir):
            filepath = os.path.join(obs_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"# 主动选股报告 — {label}\n")
                f.write(f"**日期**: {date.today().strftime('%Y-%m-%d')}\n\n")
                f.write(report)
            print(f"\n✅ 报告已保存: {filepath}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
