"""
A-share batch analysis — 批量重跑所有 A 股的多智能体分析（排除已跑过的双环传动）。

用法: PYTHONIOENCODING=utf-8 .venv/Scripts/python run_a_share_batch.py
"""
import sys
import time
import threading
from pathlib import Path
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.utils.agent_utils import load_supply_chain_context
from tradingagents.utils.token_tracker import TokenTracker

HOLDINGS_DIR = Path(r"E:\note\taihusha knowledge base\20 Areas\投资理财\03 持仓研究")
LOG_FILE = PROJECT_DIR / "a_share_batch.log"
MAX_WORKERS = 3

_LOG_LOCK = threading.Lock()

# ── Only these A-shares (双环传动 002472.SZ already done, skipped) ──
TARGETS = [
    ("神火股份", "000933.SZ"),
    ("昊华科技", "600378.SS"),
    ("京东方A",  "000725.SZ"),
    ("凯美特气", "002549.SZ"),
    ("北方稀土", "600111.SS"),
    ("华天科技", "002185.SZ"),
    ("江钨装备", "600397.SS"),
    ("沃格光电", "603773.SS"),
    ("中天科技", "600522.SS"),
    ("亨通光电", "600487.SS"),
    ("红星发展", "600367.SS"),
    ("通鼎互联", "002491.SZ"),
]


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    with _LOG_LOCK:
        print(line, flush=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def analyze_one(folder_name: str, ticker: str, analysis_date: str,
                worker_id: int, tracker: TokenTracker) -> dict:
    """Run full analysis for one stock."""
    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = 2
    config["max_risk_discuss_rounds"] = 2
    config["checkpoint_enabled"] = True

    supply_chain_ctx = load_supply_chain_context(str(HOLDINGS_DIR), folder_name)
    if supply_chain_ctx:
        log(f"  [W{worker_id}][{ticker}] Supply-chain context: {len(supply_chain_ctx)} chars")

    log(f"  [W{worker_id}][{ticker}] Starting...")
    t0 = time.perf_counter()

    ta = TradingAgentsGraph(debug=False, config=config, callbacks=[tracker])
    final_state, decision = ta.propagate(ticker, analysis_date, supply_chain_context=supply_chain_ctx)

    elapsed = time.perf_counter() - t0
    rating = decision.get("rating", "") if isinstance(decision, dict) else str(decision)[:80]
    cost = tracker.one_line()
    log(f"  [W{worker_id}][{ticker}] Done in {elapsed:.0f}s → {rating} | {cost}")
    return final_state


def build_report(ticker: str, folder_name: str, analysis_date: str, state: dict) -> str:
    lines = [
        f"# {ticker} {folder_name} — TradingAgents 完整分析",
        f"**日期**: {analysis_date} | **模型**: DeepSeek v4-pro / v4-flash | **深度**: 2",
        "", "---", "",
    ]
    sections = [
        ("一、市场/技术面分析", "market_report"),
        ("二、情绪面分析", "sentiment_report"),
        ("三、新闻与宏观分析", "news_report"),
        ("四、基本面分析", "fundamentals_report"),
    ]
    for title, key in sections:
        lines.append(f"## {title}")
        lines.append(state.get(key, "（无数据）"))
        lines.append("\n---\n")

    lines.append("## 五、牛熊辩论")
    debate = state.get("investment_debate_state", {})
    lines.append(debate.get("judge_decision", "（无数据）"))
    lines.append("\n---\n")

    lines.append("## 六、风险管理辩论")
    risk = state.get("risk_debate_state", {})
    lines.append(risk.get("judge_decision", "（无数据）"))
    lines.append("\n---\n")

    lines.append("## 七、最终投资决策")
    lines.append(state.get("final_trade_decision", "（无数据）"))
    return "\n".join(lines)


def main():
    analysis_date = date.today().strftime("%Y-%m-%d")
    tracker = TokenTracker()

    log("=" * 60)
    log(f"A-Share Batch Analysis — {analysis_date}")
    log(f"Targets: {len(TARGETS)} stocks | Workers: {MAX_WORKERS}")
    log(f"Skipped: 双环传动 (already analyzed)")
    log("=" * 60)

    success, failed = [], []
    results = []
    t_start = time.perf_counter()
    completed = 0
    total = len(TARGETS)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for i, (folder_name, ticker) in enumerate(TARGETS):
            worker_id = (i % MAX_WORKERS) + 1
            future = executor.submit(
                analyze_one, folder_name, ticker, analysis_date, worker_id, tracker
            )
            futures[future] = (folder_name, ticker)

        for future in as_completed(futures):
            folder_name, ticker = futures[future]
            completed += 1
            try:
                state = future.result()
                report = build_report(ticker, folder_name, analysis_date, state)
                report_path = HOLDINGS_DIR / folder_name / f"{analysis_date}.md"
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(report, encoding="utf-8")
                success.append(folder_name)
                results.append((folder_name, ticker, state))
                log(f"  [{completed}/{total}] ✅ {folder_name} ({ticker})")
            except Exception as e:
                log(f"  [{completed}/{total}] ❌ {folder_name} ({ticker}): {type(e).__name__}: {e}")
                import traceback
                with _LOG_LOCK:
                    with open(LOG_FILE, "a", encoding="utf-8") as f:
                        traceback.print_exc(file=f)
                failed.append((folder_name, str(e)))

            log(f"  [{completed}/{total}] {len(success)} ok / {len(failed)} failed")

    elapsed = time.perf_counter() - t_start
    log(f"\n{'='*60}")
    log(f"COMPLETE: {len(success)}/{total} in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    if failed:
        for name, err in failed:
            log(f"  ❌ {name}: {err}")
    log(f"\n{tracker.summary()}")

    # Cross-stock comparison
    if len(results) >= 2:
        log(f"\n{'='*60}")
        log("CROSS-STOCK COMPARISON")
        log(f"{'='*60}")
        from tradingagents.agents.analysts.comparison_analyst import create_comparison_analyst
        from tradingagents.llm_clients.factory import create_llm_client

        config = DEFAULT_CONFIG.copy()
        client = create_llm_client(
            provider=config["llm_provider"],
            model=config["quick_think_llm"],
            base_url=config.get("backend_url"),
        )
        llm = client.get_llm()
        compare = create_comparison_analyst(llm)

        stocks_for_cmp = [(t, n, s.get("final_trade_decision", "")) for n, t, s in results]
        try:
            cmp_report = compare(stocks_for_cmp, industry="A股全量")
            cmp_path = HOLDINGS_DIR / f"_comparisons/{analysis_date}_A股全量.md"
            cmp_path.parent.mkdir(parents=True, exist_ok=True)
            cmp_path.write_text(
                f"# A 股全量跨标的比较\n**日期**: {analysis_date}\n\n{cmp_report}",
                encoding="utf-8",
            )
            log(f"✅ Comparison saved: {cmp_path}")
        except Exception as e:
            log(f"❌ Comparison failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
