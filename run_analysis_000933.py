"""
分析 000725.SZ — DeepSeek, 研究深度 2, 断点续跑
"""
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["max_debate_rounds"] = 2
config["max_risk_discuss_rounds"] = 2
config["checkpoint_enabled"] = True

print("=" * 60)
print("TradingAgents 分析")
print(f"  股票: 000725.SZ")
print(f"  日期: 2026-06-13")
print(f"  LLM:  {config['llm_provider']}")
print(f"  深思:  {config['deep_think_llm']}")
print(f"  快思:  {config['quick_think_llm']}")
print(f"  辩论轮次: {config['max_debate_rounds']}")
print(f"  风险轮次: {config['max_risk_discuss_rounds']}")
print(f"  断点续跑: {config['checkpoint_enabled']}")
print(f"  输出语言: {config.get('output_language', 'English')}")
print("=" * 60)

ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("000725.SZ", "2026-06-13")

print("\n" + "=" * 60)
print("最终决策:")
print(decision)
print("=" * 60)
