# TradingAgents 架构全景

> 面向本 fork 开发者和重度用户的代码结构指南。README 的"Fork 状态"是功能清单，本文档是内部结构和修改入口。

---

## 一、工作流全景（端到端）

```
                        ┌─────────────────────────────┐
                        │     入口层 (Entry Points)     │
                        │  CLI / MCP Server / 脚本      │
                        └─────────────┬───────────────┘
                                      │
                        ┌─────────────▼───────────────┐
                        │   TradingAgentsGraph        │
                        │   (graph/trading_graph.py)  │
                        │   编排器：创建 LLM、注册节点  │
                        └─────────────┬───────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              │                       │                       │
    ┌─────────▼─────────┐   ┌────────▼────────┐   ┌─────────▼─────────┐
    │   GraphSetup      │   │  ConditionalLogic│   │   Propagator      │
    │   (setup.py)      │   │  (conditional_   │   │   (propagation.py)│
    │   节点 + 边注册    │   │   logic.py)      │   │   状态初始化 + 传播 │
    │   编译 StateGraph  │   │   路由决策        │   │                   │
    └─────────┬─────────┘   └────────┬────────┘   └─────────┬─────────┘
              │                       │                       │
              └───────────────────────┼───────────────────────┘
                                      │
                    ┌─────────────────▼─────────────────┐
                    │       LangGraph StateGraph         │
                    │   (分析师 → 辩论 → 决策 → 风控)      │
                    └─────────────────┬─────────────────┘
                                      │
        ┌─────────┬─────────┬─────────┼─────────┬─────────┐
        │         │         │         │         │         │
   ┌────▼────┐┌───▼───┐┌───▼───┐┌───▼───┐┌───▼───┐┌───▼───┐
   │Market   ││Social ││News   ││Fundam.││Bull/  ││Risk   │
   │Analyst  ││Analyst││Analyst││Analyst││Bear   ││Debate │
   └────┬────┘└───┬───┘└───┬───┘└───┬───┘│Debate │└───┬───┘
        │         │         │         │   └───┬───┘    │
        │         │         │         │       │        │
   ┌────▼─────────▼─────────▼─────────▼───┐   │   ┌────▼────┐
   │         Research Manager             │◄──┘   │Portfolio│
   │         (deep_think_llm)              │       │Manager  │
   └────────────────┬─────────────────────┘       │(deep)   │
                    │                             └────┬────┘
               ┌────▼────┐                            │
               │ Trader  │                            │
               └────┬────┘                            │
                    └──────────────────────────────────┘
                                      │
                        ┌─────────────▼───────────────┐
                        │   Reflection / Checkpoint   │
                        │   决策记录 + 事后验证 + 恢复   │
                        └─────────────────────────────┘
```

### 各阶段详解

| 阶段 | 节点 | 使用的模型 | 输入 | 输出 |
|------|------|-----------|------|------|
| 1. 分析师 | Market → Social → News → Fundamentals（顺序执行） | quick_think_llm | 价格数据、新闻、财务 | 四份分析报告 |
| 2. 辩论 | Bull ↔ Bear（可设轮次，默认 1 轮） | quick_think_llm | 分析师报告 + 供应链上下文 | 多空论点 |
| 3. 研判 | Research Manager | **deep_think_llm** | 辩论记录 | 结构化判断 |
| 4. 交易 | Trader | quick_think_llm | 研判结果 | 交易计划 |
| 5. 风控 | Aggressive ↔ Conservative ↔ Neutral（可设轮次） | quick_think_llm | 交易计划 | 风险评估 |
| 6. 决策 | Portfolio Manager | **deep_think_llm** | 全部 | 5 级评级 (Buy/Overweight/Hold/Underweight/Sell) |
| 7. 复盘 | Reflector | 无（确定性计算） | 历史决策 + 实际收益 | 反思文本注入下次分析 |

---

## 二、数据流层（Dataflows）

```
                    ┌─────────────────────────────────┐
                    │        interface.py              │
                    │   route_to_vendor() 统一调度      │
                    │   按配置的 vendor chain 依次尝试   │
                    └─────────────┬───────────────────┘
                                  │
        ┌─────────────┬───────────┼───────────┬─────────────┐
        │             │           │           │             │
   ┌────▼────┐  ┌────▼────┐ ┌───▼────┐ ┌───▼────┐  ┌─────▼─────┐
   │yfinance │  │akshare  │ │baostock│ │fred    │  │polymarket │
   │美股主力  │  │A股快车道│ │A股免费  │ │宏观指标 │  │预测市场    │
   │         │  │(易被封) │ │稳定备胎 │ │需APIKey│  │免Key      │
   └────┬────┘  └────┬────┘ └───┬────┘ └───┬────┘  └─────┬─────┘
        │            │          │          │             │
        │       ┌────▼────┐     │          │             │
        │       │_browser │     │          │             │
        │       │_session │     │          │             │
        │       │UA伪装   │     │          │             │
        │       └─────────┘     │          │             │
        │                       │          │             │
   ┌────▼────┐            ┌─────▼────┐     │             │
   │Alpha   │            │cn_social │     │             │
   │Vantage │            │东方财富股吧│     │             │
   │(需Key) │            │雪球/同花顺│     │             │
   └────────┘            └──────────┘     │             │
                                          │             │
                    ┌─────────────────────▼─────────────▼──┐
                    │         News Analyst (唯一同时       │
                    │    使用 FRED + Polymarket 的分析师)    │
                    └──────────────────────────────────────┘
```

### 工具 → 分析师 绑定关系

| 工具函数 | 归属于 | 被哪个分析师调用 | 数据源 |
|----------|--------|-----------------|--------|
| `get_stock_data` | `tools_market` | 市场分析师 | yfinance / Alpha Vantage |
| `get_indicators` | `tools_market` | 市场分析师 | yfinance / Alpha Vantage |
| `get_news` | `tools_social` / `tools_news` | 社交媒体 + 新闻分析师 | yfinance / Alpha Vantage / cn_social(预取) |
| `get_global_news` | `tools_news` | 新闻分析师 | yfinance / Alpha Vantage |
| **`get_macro_indicators`** ✨ | **`tools_news`** | **新闻分析师** | **FRED** |
| **`get_prediction_markets`** ✨ | **`tools_news`** | **新闻分析师** | **Polymarket** |
| `get_insider_transactions` | `tools_news` | 新闻分析师 | Alpha Vantage / yfinance |
| `get_fundamentals` | `tools_fundamentals` | 基本面分析师 | yfinance → akshare → baostock |
| `get_balance_sheet` | `tools_fundamentals` | 基本面分析师 | yfinance → akshare → baostock |
| `get_cashflow` | `tools_fundamentals` | 基本面分析师 | yfinance → akshare → baostock |
| `get_income_statement` | `tools_fundamentals` | 基本面分析师 | yfinance → akshare → baostock |

### A 股数据的三层降级链路

```
akshare (快, 实时, 东方财富抓取)
  ↓ 被封/限流 → VendorRateLimitError
baostock (免费, 注册制, 独立服务器)
  ↓ 也不可用 → NoMarketDataError
占位字符串
  "NO_DATA_AVAILABLE: No usable market data for '000933.SZ'..."
  → LLM 明确知道不可用，防止编造
```

### 错误体系

```
VendorError (基类)
├── NoMarketDataError         无可用行（空结果或过期数据）
├── VendorRateLimitError      临时限流 → 跳过到下一个供应商
└── VendorNotConfiguredError  缺少 API 密钥/配置 → 供应商不可用
```

三层错误全部被 `route_to_vendor()` 捕获，按 vendor chain 依次尝试，全部失败时返回明确哨兵。

---

## 三、核心模块

### `graph/` — 图编排

| 文件 | 职责 |
|------|------|
| `trading_graph.py` | 主 `TradingAgentsGraph` 类：创建 LLM 客户端、工具节点、编译图、运行 `propagate()` |
| `setup.py` | `GraphSetup`：注册所有节点和条件边，构建 LangGraph `StateGraph` |
| `conditional_logic.py` | 条件路由：`should_continue_*` —— 控制分析师循环 / 辩论终止 / 风控终止 |
| `propagation.py` | `Propagator`：初始化 AgentState、通过图传播状态 |
| `reflection.py` | `Reflector`：事后复盘，计算实际收益并生成反思文本 |
| `signal_processing.py` | `SignalProcessor`：从 Portfolio Manager 决策中提取 5 级评级 |
| `checkpointer.py` | 每标的 SQLite 检查点，支持崩溃恢复 |
| `analyst_execution.py` | `AnalystExecutionPlan`：定义分析师节点规范 |

### `agents/` — 智能体

| 角色 | 文件 | 功能 |
|------|------|------|
| Market Analyst | `analysts/market_analyst.py` | K 线形态、技术指标、支撑阻力 |
| Social/Sentiment Analyst | `analysts/sentiment_analyst.py` | Reddit/StockTwits + 中国社交媒体（预取注入） |
| News Analyst | `analysts/news_analyst.py` | 全球宏观 + 个股新闻 + FRED 指标 + Polymarket 预测 |
| Fundamentals Analyst | `analysts/fundamentals_analyst.py` | 财务报表分析 |
| Discovery Analyst | `analysts/discovery_analyst.py` | 基于行业/概念的选股发现 |
| Comparison Analyst | `analysts/comparison_analyst.py` | 跨标的对比分析 |
| Bull/Bear Researcher | `researchers/bull_researcher.py`, `bear_researcher.py` | 多空辩论 |
| Research Manager | `managers/research_manager.py` | 综合辩论、产生投资计划 |
| Trader | `trader/trader.py` | 创建具体交易计划 |
| Risk Debators | `risk_mgmt/` (3 个) | 激进/保守/中立三方风控讨论 |
| Portfolio Manager | `managers/portfolio_manager.py` | 最终决策 + 5 级评级 |

### `dataflows/` — 数据供应商

| 供应商 | 文件 | 覆盖面 | 密钥 |
|--------|------|--------|------|
| Yahoo Finance | `y_finance.py`, `yfinance_news.py` | 全球股票 OHLCV + 基本面 + 新闻 | 无 |
| Alpha Vantage | `alpha_vantage*.py` (5 个) | 全球股票（含技术指标、内幕交易） | `ALPHA_VANTAGE_API_KEY` |
| **akshare** | `cn_fundamentals.py`, `cn_news.py`, `cn_screener.py` | A 股基本面 + 新闻 + 筛选 | 无（抓取东方财富） |
| **baostock** | `baostock_fundamentals.py` | A 股基本面（注册制免费） | 无 |
| **FRED** | `fred.py` | 美国宏观经济指标 | `FRED_API_KEY`（免费申请） |
| **Polymarket** | `polymarket.py` | 预测市场概率 | 无 |
| Reddit | `reddit.py` | WSB/子版块情绪 | Reddit API |
| StockTwits | `stocktwits.py` | 社交媒体情绪 | 无 |
| 中国社交媒体 | `cn_social.py` | 东方财富股吧 + 雪球 + 同花顺 | 无 |

### `llm_clients/` — LLM 提供商

| 提供商 | 文件 | 备注 |
|--------|------|------|
| OpenAI | `openai_client.py` | 原生，使用 Responses API |
| Anthropic | `anthropic_client.py` | Claude |
| Google | `google_client.py` | Gemini |
| Azure | `azure_client.py` | Azure OpenAI |
| Bedrock ✨ | `bedrock_client.py` | AWS Bedrock Converse API |
| DeepSeek | `openai_client.py`（兼容模式） | OpenAI 兼容 |
| xAI (Grok) | `openai_client.py`（兼容模式） | OpenAI 兼容 |
| Qwen | `openai_client.py`（兼容模式） | 阿里云 DashScope |
| GLM | `openai_client.py`（兼容模式） | 智谱 |
| MiniMax | `openai_client.py`（兼容模式） | MiniMax |
| Kimi ✨ | `openai_client.py`（兼容模式） | Moonshot AI |
| Groq ✨ | `openai_client.py`（兼容模式） | 快速推理 |
| Nvidia ✨ | `openai_client.py`（兼容模式） | Nvidia NIM |
| Ollama | `openai_client.py`（兼容模式） | 本地模型 |

---

## 四、入口点一览

| 入口 | 文件 | 用途 |
|------|------|------|
| **CLI** | `cli/main.py` | 交互式终端界面（Typer + Rich） |
| **MCP Server** | `mcp_server.py` | Claude Code 工具注册（analyze/compare/discover/list_holdings） |
| run_analysis.py | 根目录 | 从 CLI 参数分析单标的 |
| run_a_share_batch.py | 根目录 | 并行批量分析 A 股持仓（ThreadPoolExecutor） |
| weekly_analysis.py | 根目录 | 周度全量持仓分析（周六自动） |
| discover_stocks.py | 根目录 | 主动选股：行业/概念扫描 → LLM 6 维评分 |
| generate_reports.py | 根目录 | 从 JSON 日志生成 Markdown 报告 |
| main.py | 根目录 | 最小化入口（直接 propagate） |

---

## 五、修改速查表

| 你想改什么 | 去哪个文件 |
|-----------|-----------|
| 调整 A 股数据源优先级 | `default_config.py` → `data_vendors.fundamental_data` |
| 开启/关闭中国社交媒体 | `default_config.py` → `cn_social_sources` |
| 修改新闻分析师行为/prompt | `agents/analysts/news_analyst.py` |
| 新增一个数据供应商 | `dataflows/` 下新建文件 + 在 `interface.py` 的 `VENDOR_LIST` 和 `VENDOR_METHODS` 注册 |
| 调整多智能体工作流（辩论轮数、分析师顺序） | `graph/setup.py` + `graph/conditional_logic.py` |
| 修改 MCP 工具（discover_stocks 等） | `mcp_server.py` |
| 修改 LLM 提供商配置 | `llm_clients/` + `default_config.py` |
| 修改浏览器反爬策略 | `dataflows/_browser_session.py` |
| 控制报告输出语言/格式 | `default_config.py` → `output_language` |
| 调整辩论/风控讨论轮数 | `default_config.py` → `max_debate_rounds` / `max_risk_discuss_rounds` |
| 修改持仓评分体系 | `agents/utils/rating.py` |
| 调整 Token 成本追踪 | `utils/token_tracker.py` |

---

## 六、新增功能影响总结（本 Fork vs 上游）

带 ✨ 标记的是最近合并的上游新功能，在本 fork 中的影响：

| 功能 | 对你的影响 | 需要做什么 |
|------|-----------|-----------|
| **Polymarket 预测市场** | 新闻分析师自动获取市场隐含概率（如"美联储降息 72%"），零配置即生效 | 无 |
| **FRED 宏观指标** | 新闻分析师可实时查询 CPI/失业率/利率等真实经济数据，报告更扎实 | 申请免费 [FRED API Key](https://fred.stlouisfed.org/docs/api/api_key.html)，写入 `.env` |
| **新错误体系** | akshare 被封时自动跳 baostock，全部失败时返回明确哨兵而非空字符串，LLM 不再编造数据 | 无（透明生效） |
| **新 LLM 提供商** | 如果你已有稳定的 OpenAI/DeepSeek，无需切换 | 无需操作 |
| **Baostock 稳定备胎** | A 股基本面数据有免费注册制后备，不依赖东方财富 IP 状态 | 无（已在 vendor chain 中） |
| **浏览器会话伪装** | akshare 请求自动带 Chrome UA 头，减少被封概率 | 无（import 时自动注入） |

---

## 七、与上游的关系

```
upstream (TauricResearch/TradingAgents)
  │
  ├── 我们使用的上游能力：
  │   - LangGraph 多智能体编排
  │   - yfinance 美股数据
  │   - LLM 多提供商支持
  │   - CLI 交互界面
  │   - FRED / Polymarket（合并后）
  │
  └── 本 Fork 新增：
      - A 股数据三层降级（akshare → baostock → 哨兵）
      - A 股新闻 + 品质过滤
      - 中国社交媒体情绪
      - 产业链卡点注入（serenity-skill 方法论）
      - 双时间维度评级（短线/长线独立）
      - 屏幕选股（行业/概念 → LLM 6 维评分）
      - 浏览器反爬会话（UA 头 + curl_cffi 可选）
      - MCP Server（Claude Code 集成）
      - 并行批量分析 + Token 成本追踪
```

---

*最后更新: 2026-06-14*
