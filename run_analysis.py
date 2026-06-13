"""
TradingAgents 批量分析脚本 — DeepSeek, 研究深度 2, 断点续跑, 含中国社交媒体数据
用法: python run_analysis.py <TICKER>
"""
import sys
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

if len(sys.argv) < 2:
    print("用法: python run_analysis.py <TICKER>")
    print("示例: python run_analysis.py 000933.SZ")
    sys.exit(1)

ticker = sys.argv[1].upper()
date_str = "2026-06-13"

config = DEFAULT_CONFIG.copy()
config["max_debate_rounds"] = 2
config["max_risk_discuss_rounds"] = 2
config["checkpoint_enabled"] = True

print("=" * 60)
print("TradingAgents 分析 (含中国社交媒体数据)")
print(f"  股票: {ticker}")
print(f"  日期: {date_str}")
print(f"  LLM:  {config['llm_provider']}")
print(f"  深思:  {config['deep_think_llm']}")
print(f"  快思:  {config['quick_think_llm']}")
print(f"  辩论轮次: {config['max_debate_rounds']}")
print(f"  风险轮次: {config['max_risk_discuss_rounds']}")
print(f"  断点续跑: {config['checkpoint_enabled']}")
print(f"  中国社交数据源: {config.get('cn_social_sources', [])}")
print(f"  输出语言: {config.get('output_language', 'English')}")
print("=" * 60)

ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate(ticker, date_str)

print("\n" + "=" * 60)
print(f"最终决策 ({ticker}):")
print(decision)
print("=" * 60)
