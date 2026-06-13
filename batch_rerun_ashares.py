"""
批量重跑 A 股分析 — 使用修复后的中国社交媒体数据源
用法: PYTHONIOENCODING=utf-8 .venv/Scripts/python batch_rerun_ashares.py
在 TradingAgents 目录下运行
"""
import sys
import os
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 13 A-share tickers to re-run
A_SHARE_TICKERS = [
    "603773.SS",   # 沃格光电 (test first)
    "000933.SZ",   # 神火股份
    "600378.SS",   # 昊华科技
    "002472.SZ",   # 双环传动
    "002491.SZ",   # 通鼎互联
    "002549.SZ",   # 002549
    "600111.SS",   # 北方稀土
    "000725.SZ",   # 京东方A
    "600367.SS",   # 红星发展
    "600397.SS",   # 江钨装备
    "002185.SZ",   # 华天科技
    "600522.SS",   # 中天科技
    "600487.SS",   # 亨通光电
]

DATE = "2026-06-13"


def run_one(ticker):
    """Run TradingAgents analysis for a single ticker."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = 2
    config["max_risk_discuss_rounds"] = 2
    config["checkpoint_enabled"] = True

    print(f"\n{'='*60}")
    print(f"  [{ticker}] Starting analysis...")
    print(f"{'='*60}")

    ta = TradingAgentsGraph(debug=False, config=config)
    final_state, decision = ta.propagate(ticker, DATE)

    # Extract key info for summary
    rating = ""
    if isinstance(decision, dict):
        rating = decision.get("rating", "")
    elif isinstance(decision, str):
        rating = decision[:80]

    print(f"  [{ticker}] Done. Decision: {rating}")

    return True, rating


def main():
    print("=" * 60)
    print("A-share Batch Re-run — Chinese Social Sentiment Data FIXED")
    print(f"  Date: {DATE}")
    print(f"  Tickers: {len(A_SHARE_TICKERS)}")
    print(f"  Data sources: eastmoney_guba + eastmoney_sentiment (akshare)")
    print("=" * 60)

    results = []

    for i, ticker in enumerate(A_SHARE_TICKERS, 1):
        print(f"\n[{i}/{len(A_SHARE_TICKERS)}]", end="", flush=True)
        try:
            ok, rating = run_one(ticker)
            results.append((ticker, "OK", rating))
        except Exception as e:
            results.append((ticker, "FAIL", str(e)[:100]))
            print(f"  [{ticker}] ❌ FAILED: {e}")

    # Summary
    print("\n" + "=" * 60)
    print("BATCH COMPLETE")
    print("=" * 60)
    ok_count = sum(1 for r in results if r[1] == "OK")
    fail_count = len(results) - ok_count
    print(f"  Success: {ok_count}/{len(results)}, Failed: {fail_count}")
    print()
    for ticker, status, detail in results:
        emoji = "✅" if status == "OK" else "❌"
        print(f"  {emoji} {ticker}: {detail}")
    print("=" * 60)

    # Now regenerate reports
    print("\n>>> Run generate_reports.py to update markdown files <<<")


if __name__ == "__main__":
    main()
