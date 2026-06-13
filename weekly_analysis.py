"""
周度持仓基本面分析 — 每周六 9:00 自动运行
用法: PYTHONIOENCODING=utf-8 .venv/Scripts/python weekly_analysis.py

扫描持仓研究目录下所有标的文件夹，对每个标的运行完整 TradingAgents 分析流程，
将结果保存为 <标的文件夹>/<日期>.md。

v2: 并行分析模式 — 多个标的同时分析，大幅缩短总耗时。
    通过 MAX_WORKERS 控制并发数（默认 3）。
"""
import sys
import os
import re
import json
import time
import threading
from pathlib import Path
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── 配置 ──
PROJECT_DIR = Path(__file__).resolve().parent
HOLDINGS_DIR = Path(r"E:\note\taihusha knowledge base\20 Areas\投资理财\03 持仓研究")
LOG_FILE = PROJECT_DIR / "weekly_analysis.log"

# 并行分析最大并发数（按 LLM API 限流和内存综合考虑）
MAX_WORKERS = int(os.environ.get("WEEKLY_MAX_WORKERS", "3"))

# 跳过的关键词（ETF、指数、卡片等非个股）
SKIP_KEYWORDS = ["ETF", "上证", "科创", "创AI", "富国", "持仓卡片", "观察", "index", "etf"]

# 线程安全的日志锁
_LOG_LOCK = threading.Lock()


def log(msg: str):
    """Write timestamped log message (thread-safe)."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    with _LOG_LOCK:
        print(line, flush=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def discover_holdings() -> list[tuple[str, str, str]]:
    """Scan holdings directory for stock folders.

    Returns list of (folder_name, ticker, readme_path).
    """
    holdings = []
    for d in sorted(HOLDINGS_DIR.iterdir()):
        if not d.is_dir():
            continue
        # Skip backup dirs
        if d.name.startswith("_") or d.name.startswith("."):
            continue
        # Skip ETF/index keywords
        if any(kw in d.name for kw in SKIP_KEYWORDS):
            log(f"  SKIP {d.name} — ETF/index")
            continue

        # Read ticker from README.md frontmatter
        readme = d / "README.md"
        ticker = None
        if readme.exists():
            with open(readme, "r", encoding="utf-8") as f:
                content = f.read(500)
            m = re.search(r"ticker:\s*(\S+)", content)
            if m:
                ticker = m.group(1).strip()
            else:
                ticker = _infer_ticker(d.name)

        if ticker:
            holdings.append((d.name, ticker, str(readme)))
            log(f"  FOUND {d.name} → {ticker}")
        else:
            log(f"  SKIP {d.name} — no ticker found")

    return holdings


def _infer_ticker(folder_name: str) -> str | None:
    """Infer ticker from folder name for known stocks."""
    KNOWN = {
        "神火股份": "000933.SZ",
        "昊华科技": "600378.SS",
        "双环传动": "002472.SZ",
        "通鼎互联": "002491.SZ",
        "凯美特气": "002549.SZ",
        "北方稀土": "600111.SS",
        "京东方A": "000725.SZ",
        "红星发展": "600367.SS",
        "江钨装备": "600397.SS",
        "华天科技": "002185.SZ",
        "沃格光电": "603773.SS",
        "中天科技": "600522.SS",
        "亨通光电": "600487.SS",
        "NOW": "NOW",
        "MRVL": "MRVL",
        "NOK": "NOK",
        "IREN": "IREN",
        "DRAM": "DRAM",
        "TEAM": "TEAM",
        "PATH": "PATH",
        "NVDA": "NVDA",
        "TSM": "TSM",
        "AVGX": "AVGX",
        "RDW": "RDW",
    }
    return KNOWN.get(folder_name)


def analyze_single(folder_name: str, ticker: str, analysis_date: str, worker_id: int) -> dict:
    """Run full TradingAgents analysis for one stock (thread-safe worker).

    Each worker creates its own TradingAgentsGraph instance to avoid
    shared-state contention. Returns the final state dict, or raises.
    """
    # Import inside the worker so each thread gets fresh module state
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.agents.utils.agent_utils import load_supply_chain_context

    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = 2
    config["max_risk_discuss_rounds"] = 2
    config["checkpoint_enabled"] = True

    # Load pre-computed supply-chain / bottleneck analysis.
    supply_chain_ctx = load_supply_chain_context(str(HOLDINGS_DIR), folder_name)
    if supply_chain_ctx:
        log(f"  [W{worker_id}][{ticker}] Supply-chain context loaded ({len(supply_chain_ctx)} chars)")
    else:
        log(f"  [W{worker_id}][{ticker}] No supply-chain context — consider /serenity-skill")

    log(f"  [W{worker_id}][{ticker}] Starting analysis...")
    t0 = time.perf_counter()

    ta = TradingAgentsGraph(debug=False, config=config)
    final_state, decision = ta.propagate(ticker, analysis_date, supply_chain_context=supply_chain_ctx)

    elapsed = time.perf_counter() - t0
    rating = ""
    if isinstance(decision, dict):
        rating = decision.get("rating", "")
    elif isinstance(decision, str):
        rating = decision[:80]

    log(f"  [W{worker_id}][{ticker}] Done in {elapsed:.0f}s: {rating}")

    return final_state


def build_report(ticker: str, folder_name: str, analysis_date: str, state: dict) -> str:
    """Build a markdown report from the analysis state."""
    lines = []
    lines.append(f"# {ticker} {folder_name} — TradingAgents 完整分析")
    lines.append("")
    lines.append(f"**日期**: {analysis_date} | **模型**: DeepSeek v4-pro / v4-flash | **研究深度**: 2")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 1-4. Four analyst reports
    sections = [
        ("一、市场 / 技术面分析（Market Analyst）", "market_report"),
        ("二、情绪面分析（Sentiment Analyst）", "sentiment_report"),
        ("三、新闻与宏观分析（News Analyst）", "news_report"),
        ("四、基本面分析（Fundamentals Analyst）", "fundamentals_report"),
    ]

    for title, key in sections:
        lines.append(f"## {title}")
        lines.append("")
        content = state.get(key, "")
        lines.append(content if content else "（无数据）")
        lines.append("")
        lines.append("---")
        lines.append("")

    # 5. Bull vs Bear debate
    lines.append("## 五、牛熊辩论（Bull vs Bear Debate）")
    lines.append("")
    debate = state.get("investment_debate_state", {})
    judge = debate.get("judge_decision", "")
    if judge:
        lines.append(judge)
    else:
        lines.append("（无辩论数据）")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 6. Risk management debate
    lines.append("## 六、风险管理辩论（Risk Management Debate）")
    lines.append("")
    risk = state.get("risk_debate_state", {})
    risk_judge = risk.get("judge_decision", "")
    if risk_judge:
        lines.append(risk_judge)
    else:
        lines.append("（无风控数据）")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 7. Final decision
    lines.append("## 七、最终投资决策（Final Trade Decision）")
    lines.append("")
    final = state.get("final_trade_decision", "")
    lines.append(final if final else "（无最终决策数据）")
    lines.append("")

    return "\n".join(lines)


def save_report(folder_name: str, analysis_date: str, report: str):
    """Save the analysis report to the holding's folder."""
    folder_path = HOLDINGS_DIR / folder_name
    folder_path.mkdir(parents=True, exist_ok=True)

    report_path = folder_path / f"{analysis_date}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    log(f"  [{folder_name}] Report saved: {report_path}")


def main():
    analysis_date = date.today().strftime("%Y-%m-%d")

    log("=" * 60)
    log(f"Weekly Analysis — {analysis_date}")
    log(f"Parallel workers: {MAX_WORKERS}")
    log("=" * 60)

    # 1. Discover holdings
    log("Discovering holdings...")
    holdings = discover_holdings()
    log(f"  Total: {len(holdings)} stocks to analyze")

    if not holdings:
        log("No holdings found — exiting.")
        return

    # 2. Run analysis in parallel
    success, failed = [], []
    t_start = time.perf_counter()
    completed = 0
    total = len(holdings)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit all jobs
        futures = {}
        for i, (folder_name, ticker, _) in enumerate(holdings):
            worker_id = (i % MAX_WORKERS) + 1
            future = executor.submit(analyze_single, folder_name, ticker, analysis_date, worker_id)
            futures[future] = (folder_name, ticker)

        # Process results as they complete
        for future in as_completed(futures):
            folder_name, ticker = futures[future]
            completed += 1
            try:
                state = future.result()
                report = build_report(ticker, folder_name, analysis_date, state)
                save_report(folder_name, analysis_date, report)
                success.append(folder_name)
                log(f"  [{completed}/{total}] ✅ {folder_name} ({ticker}) — report saved")
            except Exception as e:
                log(f"  [{completed}/{total}] ❌ {folder_name} ({ticker}) FAILED: {type(e).__name__}: {e}")
                # Log full traceback for debugging
                import traceback
                with _LOG_LOCK:
                    with open(LOG_FILE, "a", encoding="utf-8") as f:
                        f.write(f"  Traceback for {ticker}:\n")
                        traceback.print_exc(file=f)
                        f.write("\n")
                failed.append((folder_name, str(e)))

            log(f"  [{completed}/{total}] Progress: {len(success)} ok / {len(failed)} failed")

    # 3. Summary
    elapsed = time.perf_counter() - t_start
    log("\n" + "=" * 60)
    log(f"COMPLETE: {len(success)}/{total} succeeded in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    if failed:
        for name, err in failed:
            log(f"  ❌ {name}: {err}")
    log(f"Avg per stock: {elapsed/total:.0f}s (parallel with {MAX_WORKERS} workers)")
    log("=" * 60)


if __name__ == "__main__":
    main()
