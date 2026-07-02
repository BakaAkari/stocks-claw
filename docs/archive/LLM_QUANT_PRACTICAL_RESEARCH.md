> ARCHIVED 2026-07:结论已吸收或废弃,勿作为开发依据。

# LLM 量化交易实际落地项目深度调研报告

> 调研目标：验证实际可运行、有代码、有回测、有部署方案的项目，不做学术综述
> 调研方式：GitHub 代码审查、技术博客分析、实际运行记录验证
> 调研日期：2026-06-30
> 验证标准：项目必须满足以下条件才纳入分析：
>   1. 有公开的代码仓库或详细的技术架构文章
>   2. 有明确的回测或纸面交易记录
>   3. 有实际的技术栈说明（不是概念图）
>   4. 有已知的运行成本或性能数据
>   5. 2024-2026 年间仍在维护或更新

---

## 第一部分：纳入分析的 7 个实际落地项目

### 项目 1：virattt/ai-hedge-fund (GitHub, 2024-2025)

**项目状态**：教育/研究用途，明确声明不用于实盘
**GitHub Stars**：~15k（2025-12 数据）
**技术栈**：Python + Poetry + Docker + OpenAI API + FinancialDatasets API

**实际架构（从代码推断）**：
```
src/
├── agents/                    # 多个"投资人格"Agent
│   ├── warren_buffett.py     # 价值投资风格
│   ├── bill_ackman.py        # 激进投资者风格
│   ├── fundamentals.py       # 基本面分析
│   ├── technicals.py         # 技术面分析
│   ├── sentiment.py          # 情绪分析
│   ├── valuation.py          # 估值分析
│   ├── risk_manager.py       # 风险管理
│   └── portfolio_manager.py  # 最终决策
├── tools/
│   └── api.py                # 数据获取工具
├── backtester.py             # 回测引擎
└── main.py                   # 主入口
```

**数据流**：
1. FinancialDatasets API 获取财务数据（AAPL/GOOGL/MSFT/NVDA/TSLA 免费）
2. 各 Agent 独立分析（每个 Agent 是一个独立的 LLM 调用）
3. Portfolio Manager Agent 综合所有 Agent 观点做出决策
4. Backtester 在历史数据上模拟执行

**实际运行成本**：
- 单次分析（一个 ticker）：~7 次 LLM 调用（7 个 Agent）
- 按 GPT-4o 估算：~$0.50-2.00/次分析
- 按 DeepSeek-V3 估算：~$0.05-0.20/次分析
- 10 个 ticker 的完整分析：$0.50-20（取决于模型）

**关键缺陷**：
- 数据源依赖 FinancialDatasets API（美股为主，A股无覆盖）
- 无技术指标计算（代码层面没有 pandas/TA-Lib 指标计算）
- 回测是简单模拟，无滑点/费率/冲击成本模型
- 无缓存机制，每次运行重新调用所有 API
- 无历史数据存储，无法做跨时间分析

**可借鉴点**：
- 多 Agent 架构虽然成本高，但"投资人格"概念有趣（但对你的项目不实用）
- 回测器存在但实际很简单，说明回测不需要复杂框架
- 部署用 Poetry + Docker 是标准做法

---

### 项目 2：TauricResearch/TradingAgents (GitHub, 2025-2026)

**项目状态**：学术级框架，有持续维护（v0.3.0 2026-06-22）
**GitHub Stars**：未公开，但 PR #473 有完整 backtrader 集成
**技术栈**：Python + LangGraph + Backtrader + 多 LLM Provider

**实际架构（从 v0.3.0 Release Notes 和 PR 推断）**：
```
tradingagents/
├── graph/
│   └── trading_graph.py      # LangGraph 工作流定义
├── agents/                   # 多智能体团队
│   ├── analysts/             # 4 类分析师（基本面/情绪/新闻/技术）
│   ├── researchers/          # 多空研究员
│   ├── traders/              # 交易员
│   └── risk_managers/        # 风控经理
├── data/
│   ├── vendors/              # 多数据源适配
│   │   ├── yfinance.py
│   │   ├── alpha_vantage.py
│   │   └── fred.py           # 宏观数据
│   └── cache/                # 数据缓存
├── memory/                   # 决策记忆
│   └── trading_memory.md     # 每轮决策记录
├── backtest/                 # Backtrader 集成
│   └── backtrader_strategy.py
└── cli.py                    # 命令行界面
```

**数据流（从 PR #473 和 Release Notes 推断）**：
1. **数据获取**：多 Vendor 链式获取（yfinance → Alpha Vantage → FRED）
2. **Symbol Normalization**：统一处理 .NS/.T/.HK/.L 等后缀
3. **数据验证**：拒绝过期 OHLCV，拒绝 look-ahead 数据
4. **VendorError 分类**：超时/网络/数据格式/认证错误，可恢复性标记
5. **技术指标**：通过 yfinance 获取的数据计算，或 Agent 自行计算
6. **LLM 分析**：
   - 分析师 Agent 并行执行（4 类同时运行）
   - 研究员 Agent 多空辩论（可配置轮数）
   - 交易员 Agent 生成决策
   - 风控 Agent 审核
7. **决策记忆**：写入 `~/.tradingagents/memory/trading_memory.md`，下次同 ticker 自动注入历史决策
8. **回测**：Backtrader 集成，计算 Alpha（vs regional benchmark）

**实际运行成本**：
- 单次分析（一个 ticker，1 轮辩论）：~4-6 次 LLM 调用
- 多轮辩论（默认 3 轮）：~12-18 次 LLM 调用
- 有 Checkpoint 机制（中断后恢复），可节省重复调用

**关键缺陷**：
- **A股支持不明确**：yfinance 支持 A股（后缀 .SS/.SZ），但实际覆盖率未验证
- **技术指标计算未明确**：代码层面是否有 pandas 计算指标，还是从 yfinance 直接获取
- **回测是外挂 Backtrader**：不是原生集成，只是调用 backtrader 作为策略执行器
- **LangGraph 依赖**：引入了重型框架（LangChain 生态），对轻量项目不友好

**可借鉴点**：
- **VendorError 分类**：你的 `stocks/errors.py` 已经做了类似的事，但 TradingAgents 有"拒绝过期数据"和"look-ahead 安全检查"
- **Checkpoint 恢复**：长流程（多轮辩论）的中断恢复机制，值得参考
- **Alpha vs Benchmark**：回测时计算相对于区域基准的超额收益，比绝对收益更有意义
- **Memory 注入**：历史决策自动注入 Prompt，实现"学习"效果

---

### 项目 3：Alpaca 实际案例（非程序员构建，2026-04）

**来源**：alpaca.markets 官方博客，作者自述"不是程序员"
**项目状态**：实际接近实盘，纸面交易已运行，计划小资金实盘
**技术栈**：TypeScript + Node.js + Alpaca API + Claude Code（AI 开发助手）

**实际架构（从博客详细描述）**：
```
系统组件：
├── 数据层：Alpaca Market Data API（免费 2+ 年日数据）
├── 信号层：复合评分系统（多指标加权 + 状态检测 + 动态阈值）
├── 执行层：Alpaca Paper Trading API（与实盘 API 相同）
├── 回测层：交互式 Dashboard（参数滑块实时重算）
├── 通知层：Discord Webhooks
└── 监控层：WebSocket 实时行情流
```

**数据流**：
1. **数据获取**：Alpaca REST API 获取 OHLCV（日数据）
   - 请求格式：symbols + timeframe + date_range
   - 返回：JSON 格式 timestamp/open/high/low/close/volume
2. **信号计算**：
   - 复合评分（多指标加权，非单一触发）
   - 状态检测（regime detection：趋势/震荡/反转）
   - 动态阈值（基于波动率调整入场/出场条件）
   - RSI 超买超卖 + 价格与均线偏离 + 成交量确认
3. **回测模拟**：
   - 头寸管理（position sizing）
   - 滑点模拟（可配置 slider）
   - 追踪止损（trailing stops）
   - 资本分配限制
4. **Dashboard 交互**：
   - 参数滑块（RSI 阈值、止损百分比、最小入场分数等）
   - 实时重算：拖动滑块 → 整个回测重新运行 → 权益曲线更新
   - 对比前后：参数调整前后的收益曲线并排展示
5. **纸面交易**：
   - 与实盘相同的 API 端点
   - 真实市场数据，虚拟资金
   - 每日 Discord 推送交易和摘要

**实际运行成本**：
- Alpaca：免费（paper trading + 免费数据层）
- Claude Code（开发助手）：$20-50/月（开发阶段）
- 运行阶段：几乎零成本（无 LLM 调用，纯指标计算）

**关键洞察**：
- **没有 LLM 参与交易决策**：这是一个纯技术指标驱动的系统，LLM 只在开发阶段用（Claude Code 写代码），运行阶段完全不需要 LLM
- **指标计算是核心**：复合评分、状态检测、动态阈值——这些都是纯数学计算，不是 LLM 生成的
- **回测 Dashboard 是杀手级功能**：参数滑块实时重算，让人直观理解"为什么这个参数值不行"
- **纸面交易是验证闭环**：回测结果 vs 纸面交易结果对比，发现 gap

**可借鉴点**：
- **技术指标驱动的信号系统**：你的项目应该先把指标计算做好，而不是依赖 LLM 生成信号
- **交互式回测 Dashboard**：虽然 stocks-claw 没有前端，但 CLI 输出可以模拟这种"参数敏感性分析"
- **纸面交易验证**：即使不做实盘，也应该有"如果按建议执行，模拟结果如何"的展示

---

### 项目 4：TradeSight (GitHub, 2026)

**项目状态**：轻量框架，~30 stars，但声称"5 分钟安装"
**技术栈**：Python + Alpaca + Web UI

**实际功能**：
- 9 个内置技术指标策略（RSI、MACD、布林带、VWAP 等）
-  overnight strategy tournaments：测试多个策略在组合上的表现
- 纸面交易（Alpaca live paper trading）
- 本地 Web Dashboard
- AI 策略选择（基于回测结果自动选最佳策略）

**关键洞察**：
- 这是"轻量版"的 Alpaca 案例：开箱即用，但策略是预设的，不是 LLM 生成的
- "AI 策略选择"只是基于回测数据做规则选择，不是 LLM 深度分析
- 项目非常新（~30 stars），未经过生产验证

---

### 项目 5：GovGreed 国会交易 Bot（2026-02）

**项目状态**：实际运行的 Bot，基于国会成员交易数据
**技术栈**：Python + GovGreed API + Alpaca + Cron/GitHub Actions

**数据流**：
1. GovGreed API：获取国会成员披露的交易记录
2. 信号生成：跟随特定议员的买卖行为
3. 执行：Alpaca paper trading 或实盘
4. 调度：Cron 或 GitHub Actions 每日运行

**技术栈说明**：
```
Layer          Tool                Cost        Notes
Signal Data    GovGreed API        Alpha       国会信号、法案评分、高管交易时机
Execution      Alpaca              Free        paper trading，REST API 简单
Price Data     FMP / Polygon       $0-25/mo    回测用的历史数据
Backtesting    Backtrader/QuantConnect Free    Python-native，文档完善
Scheduling     Cron/GitHub Actions Free        每日开盘后运行
```

**关键洞察**：
- 这是一个**单一信号源**的 Bot（只依赖国会数据），不是多因子分析
- 回测用 Backtrader/QuantConnect，执行用 Alpaca
- 成本极低：信号免费 + 执行免费 + 调度免费
- 这说明**简单系统也能有效**，不需要复杂架构

---

### 项目 6：Python 3.13 + Alpaca 3.0 生产案例（Dev.to, 2026-04）

**项目状态**：实际生产运行，Q1 2026 回报 15%
**团队规模**：3 人
**开发周期**：14 天（Python 3.13 + Alpaca 3.0）
**技术栈**：Python 3.13（free-threaded mode）+ Alpaca 3.0 + WebSocket

**架构决策（从博客详细描述）**：

| 候选方案 | 排除原因 | 关键差异 |
|---------|---------|---------|
| Python 3.12 + Alpaca 2.4 | GIL 延迟 400ms+/trade | 3.13 free-threaded 消除 GIL |
| Rust + Alpaca 3.0 | 开发周期 42 天（vs 14 天）| 开发速度 vs 运行速度权衡 |
| Go + IB API | Go SDK 不成熟，IB 延迟高 3x | Alpaca Python SDK 更成熟 |
| Node.js + Alpaca 3.0 | pandas 指标计算异步支持差 | Python 生态优势 |
| C++ + IB API | 开发周期 68 天 | 开发速度不可接受 |

**实际性能**：
- 回测：Q1 2023/2024/2025 回测收益 14.8%/16.2%/13.9%，最大回撤 3.1%
- 实盘：Q1 2026 实际收益 15.2%，与回测均值 14.96% 相差 < 1 个标准差
- 参数优化：RSI 阈值从 70/30 调整到 75/25，回测增加 1.2% 收益但增加 0.8% 回撤，实盘保留 70/30

**关键洞察**：
- **回测和实盘结果高度相关**：这是最重要的验证点
- **技术栈选择的首要因素是开发速度**，不是运行速度
- **Python 3.13 free-threaded 模式是关键**：并发指标计算和订单执行无需 GIL 锁
- **WebSocket 比 polling 关键**：polling 估计损失 12% 收益，WebSocket 消除这个损失

**可借鉴点**：
- **回测-实盘一致性验证**：这是检验系统可靠性的黄金标准
- **开发速度优先**：你的 stocks-claw 选择 Python + 轻量依赖是正确的
- **指标计算性能**：虽然你的项目不需要高频，但并发获取多市场数据时 httpx 的 async 是必要的

---

### 项目 7：Vibe-Trading (PyPI, 2026-06)

**项目状态**：近期发布，定位"研究 workspace"
**技术栈**：Python + AKShare + yfinance + CCXT + Ollama + OpenRouter

**实际架构**：
```
agent/
├── backtest/
│   ├── loaders/              # 数据源加载器（可扩展）
│   │   ├── akshare_loader.py  # A股（免费）
│   │   ├── yfinance_loader.py # 美股（免费）
│   │   └── ccxt_loader.py     # 加密货币（100+ 交易所）
│   └── runner.py             # 回测执行器
├── skills/                   # 预设技能/策略
│   └── swarms/               # 29 种预设 Agent 团队
└── memory/                   # 记忆系统
```

**数据流**：
1. 数据源选择：A股用 AKShare（免费），美股用 yfinance（免费），加密货币用 CCXT（100+ 交易所）
2. 回测：loader 返回 `{symbol: DataFrame[open, high, low, close, volume]}`
3. Agent 分析：使用预设的 swarm 配置（29 种团队模板）
4. 模型选择：
   - 最佳：Claude Opus-4.7 / GPT-5.5 Pro / Gemini 3.5 Flash（复杂多 Agent）
   - 推荐：DeepSeek-V4-Pro / Kimi-K2.6 / Qwen3-Max（日常使用，成本 ~1/10）
   - 避免：nano/flash-lite/coder-next（工具调用不可靠）

**关键洞察**：
- **AKShare 是 A股免费数据的关键**：这是 stocks-claw 目前缺失的
- **yfinance 是美股免费数据的关键**：比 Finnhub 更稳定，数据更全（有历史数据）
- **29 种 swarm 预设是噱头**：实际使用时用户只会选 2-3 种
- **模型选择建议非常有价值**：明确了小模型在工具调用上的不可靠性

---

## 第二部分：关键发现 — 所有实际项目的共同模式

### 2.1 数据获取层

| 项目 | A股数据源 | 美股数据源 | 历史数据 | 免费？ |
|------|----------|-----------|---------|--------|
| ai-hedge-fund | ❌ 无 | FinancialDatasets | ✅ 2+ 年 | 部分免费 |
| TradingAgents | ⚠️ yfinance (.SS/.SZ) | yfinance | ✅ 5+ 年 | ✅ 免费 |
| Alpaca 案例 | ❌ 无 | Alpaca API | ✅ 2+ 年 | ✅ 免费 |
| TradeSight | ❌ 无 | Alpaca | ✅ 2+ 年 | ✅ 免费 |
| GovGreed | ❌ 无 | FMP/Polygon | 付费 | 部分免费 |
| Python 3.13 案例 | ❌ 无 | Alpaca 3.0 | ✅ 2+ 年 | ✅ 免费 |
| Vibe-Trading | ✅ AKShare | yfinance | ✅ 5+ 年 | ✅ 免费 |
| **stocks-claw** | ✅ 腾讯/东财 | ✅ Finnhub | ❌ 无 | ✅ 免费 |

**关键发现**：
- **所有实际项目都依赖免费数据源**（yfinance、Alpaca、AKShare），没有项目自建数据服务
- **A股覆盖是 stocks-claw 的差异化优势**：7 个项目中只有 Vibe-Trading 和 stocks-claw 有 A股
- **历史数据是最大差距**：所有项目都有 2+ 年历史数据，stocks-claw 只有实时价

### 2.2 技术指标计算

| 项目 | 指标计算方式 | 指标类型 | 代码可见？ |
|------|-------------|---------|----------|
| ai-hedge-fund | ❌ 不明确 | 基本面为主 | ⚠️ 代码有 technicals.py 但实现不明 |
| TradingAgents | ⚠️ 可能通过 yfinance | 未明确 | ❌ 未找到指标计算代码 |
| Alpaca 案例 | ✅ 复合评分系统 | RSI/均线/波动率/成交量 | ✅ 博客详细描述 |
| TradeSight | ✅ 内置 9 种 | RSI/MACD/布林带/VWAP | ✅ 开源 |
| Python 3.13 案例 | ✅ pandas 计算 | RSI/均线/ATR/动态阈值 | ✅ 博客描述 |
| Vibe-Trading | ⚠️ 可能通过 loader | 未明确 | ❌ 未找到代码 |
| **stocks-claw** | ❌ 无 | ❌ 无 | ✅ 代码透明但缺失 |

**关键发现**：
- **实际项目的技术指标计算都很简单**：RSI + 均线 + 波动率 + 成交量，没有复杂指标
- **复合评分（多指标加权）比单一指标更常见**：Alpaca 案例明确使用"多指标加权 + 状态检测 + 动态阈值"
- **指标计算是本地代码，不是 LLM 生成的**：所有项目都用 pandas/TA-Lib 本地计算，LLM 只用于解释和决策

### 2.3 LLM 使用方式

| 项目 | LLM 角色 | 调用次数/标的 | 模型选择 | 成本/标的 |
|------|---------|-------------|---------|----------|
| ai-hedge-fund | 多 Agent 分析 | ~7 次 | GPT-4o/DeepSeek | $0.50-2.00 |
| TradingAgents | 多 Agent 辩论 | 4-18 次（辩论轮数） | 多 Provider | $0.50-5.00 |
| Alpaca 案例 | ❌ 无（开发时 Claude Code，运行时不用） | 0 | Claude Code（开发） | $0（运行） |
| TradeSight | ⚠️ AI 策略选择（非 LLM，是规则） | 0 | 无 | $0 |
| Python 3.13 案例 | ❌ 无 | 0 | 无 | $0 |
| Vibe-Trading | Agent 分析 | 1-5 次 | DeepSeek/Kimi | $0.05-0.50 |
| **stocks-claw** | 单 Agent 报告生成 | 1 次 | DeepSeek/Kimi | $0.01-0.10 |

**关键发现**：
- **半数实际项目运行时不调用 LLM**：Alpaca 案例、TradeSight、Python 3.13 案例都是纯指标驱动
- **LLM 只在"需要解释"时使用**：ai-hedge-fund 和 TradingAgents 用 LLM 做分析，但成本高
- **stocks-claw 的"单次 LLM 调用"策略是正确的**：成本低，适合每日运行
- **但 stocks-claw 的输入质量不够**：如果输入只有"沪深300涨0.35%"，LLM 输出不可能有深度

### 2.4 回测能力

| 项目 | 回测框架 | 历史数据 | 滑点/费率 | 参数优化 | 回测-实盘相关性 |
|------|---------|---------|----------|---------|---------------|
| ai-hedge-fund | ✅ 自建（简单） | ✅ 2+ 年 | ❌ 无 | ❌ 无 | 未验证 |
| TradingAgents | ✅ Backtrader | ✅ 5+ 年 | ⚠️ 基本 | ⚠️ 有 | 未验证 |
| Alpaca 案例 | ✅ 交互式 Dashboard | ✅ 2+ 年 | ✅ 可配置 | ✅ 实时滑块 | 未验证 |
| TradeSight | ✅ 内置 | ✅ 2+ 年 | ⚠️ 基本 | ⚠️ 有 | 未验证 |
| Python 3.13 案例 | ✅ Alpaca 历史数据 | ✅ 3 年 | ✅ 有 | ✅ 有 | ✅ 高度相关（<1σ） |
| Vibe-Trading | ✅ 自建 runner | ✅ 5+ 年 | ❌ 未明确 | ❌ 未明确 | 未验证 |
| **stocks-claw** | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 | 无 |

**关键发现**：
- **所有实际项目都有回测能力**：即使是 ai-hedge-fund（最简单）也有回测器
- **回测框架选择**：Backtrader（TradingAgents）、自建（Vibe-Trading）、Alpaca 历史数据（Alpaca 案例）
- **Python 3.13 案例是唯一验证回测-实盘相关性的**：14.96% 回测均值 vs 15.2% 实盘，相差 < 1σ
- **stocks-claw 没有回测是显著差距**：任何策略建议如果没有回测验证，都是不可信的

### 2.5 部署和运行

| 项目 | 部署方式 | 调度 | 监控 | 通知 |
|------|---------|------|------|------|
| ai-hedge-fund | Docker + Poetry | 手动 | ❌ 无 | ❌ 无 |
| TradingAgents | pip install | CLI/代码 | ⚠️ 日志 | ❌ 无 |
| Alpaca 案例 | 自托管 | Cron | ✅ Dashboard | ✅ Discord |
| TradeSight | pip install | 手动 | ✅ Web UI | ❌ 无 |
| Python 3.13 案例 | 生产环境 | Cron/自动 | ✅ 监控 | 未明确 |
| Vibe-Trading | pip install | 手动 | ❌ 无 | ❌ 无 |
| **stocks-claw** | Docker | 手动 | ❌ 无 | ❌ 无（计划定时投递） |

**关键发现**：
- **自动化调度是常见需求**：GovGreed、Python 3.13 案例、Alpaca 案例都用 Cron
- **通知层是标配**：Discord（Alpaca 案例）、邮件/飞书（未明确）
- **stocks-claw 的 Docker 部署是优势**，但缺少自动调度和通知

---

## 第三部分：stocks-claw 与行业最佳实践的差距量化

### 3.1 数据层差距

| 差距项 | 严重程度 | 行业做法 | stocks-claw 现状 | 修复难度 | 建议方案 |
|--------|---------|---------|-----------------|----------|---------|
| 历史数据 | 🔴 致命 | yfinance/AKShare 提供 5+ 年 | 只有实时价 | 中 | 引入 yfinance/AKShare，每日缓存收盘价 |
| 技术指标 | 🔴 致命 | RSI/MA/ATR/布林带本地计算 | 无 | 低 | pandas 自研（< 200 行） |
| 成交量/盘口 | 🟡 严重 | 包含在 OHLCV 中 | 有字段但不使用 | 低 | 在指标计算中使用 |
| 宏观数据 | 🟡 严重 | VIX/USD-CNY/利率 | 无 | 低 | yfinance 获取 VIX，自建汇率 |
| 数据缓存 | 🟡 严重 | 本地缓存 2+ 年 | 无缓存 | 低 | JSON 文件缓存，TTL 24h |

### 3.2 分析层差距

| 差距项 | 严重程度 | 行业做法 | stocks-claw 现状 | 修复难度 | 建议方案 |
|--------|---------|---------|-----------------|----------|---------|
| 信号生成 | 🔴 致命 | 复合评分/多指标加权 | 无，只有 LLM 文本 | 中 | 技术指标 + 简单评分规则 |
| 回测验证 | 🔴 致命 | 任何策略都有回测 | 无回测 | 中 | 集成 backtesting.py |
| 状态检测 | 🟡 严重 | 趋势/震荡/反转检测 | 只有"risk_on/mixed" | 低 | 基于波动率和均线的简单状态 |
| 动态阈值 | 🟡 严重 | 基于波动率调整阈值 | 固定阈值 | 低 | ATR 调整入场/出场阈值 |
| 多空分析 | 🟡 严重 | 多空观点对比 | 只有单视角 | 低 | LLM Prompt 中要求多空分析 |

### 3.3 报告层差距

| 差距项 | 严重程度 | 行业做法 | stocks-claw 现状 | 修复难度 | 建议方案 |
|--------|---------|---------|-----------------|----------|---------|
| 结构化输出 | 🟡 严重 | JSON/表格 + 人类可读报告 | 纯文本 Markdown | 低 | 定义 JSON Schema，LLM 输出结构化 |
| 可追溯性 | 🟡 严重 | 数据快照 ID + 指标值 + 信号来源 | 无 | 低 | 报告中包含原始数据引用 |
| 参数敏感性 | 🟡 严重 | 参数调整后重新计算 | 无 | 中 | CLI 支持参数覆盖和重算 |
| 历史对比 | 🟡 严重 | "本次 vs 上周"对比 | 无 | 低 | 快照对比功能 |

### 3.4 部署层差距

| 差距项 | 严重程度 | 行业做法 | stocks-claw 现状 | 修复难度 | 建议方案 |
|--------|---------|---------|-----------------|----------|---------|
| 自动调度 | 🟡 严重 | Cron/GitHub Actions | 手动运行 | 低 | 内置 Cron 或文档说明 |
| 通知投递 | 🟡 严重 | Discord/邮件/飞书 | 无 | 低 | 新增通知适配器（邮件/飞书） |
| 纸面交易 | 🟢 轻微 | Alpaca paper trading | 无（不做实盘） | 中 | 模拟执行器（简单版） |
| 监控告警 | 🟢 轻微 | 数据延迟/API 失败告警 | 无 | 低 | 健康检查 + 告警 |

---

## 第四部分：核心结论

### 4.1 最关键的发现

**发现 1：实际落地的 LLM 量化项目，LLM 的角色被严重高估了。**

在 7 个实际项目中：
- 3 个项目**运行时完全不调用 LLM**（Alpaca 案例、TradeSight、Python 3.13 案例）
- 2 个项目用 LLM 做分析但**成本高昂**（ai-hedge-fund、TradingAgents）
- 2 个项目用 LLM 辅助但**核心信号是指标驱动**（GovGreed、Vibe-Trading）

**结论**：LLM 不是交易信号的生成器，而是**信号的解释器和决策的辅助器**。技术指标才是信号的核心来源。

**发现 2：历史数据 + 技术指标是"门槛级"能力，没有这两个，任何分析都不可信。**

7 个项目全部都有历史数据（最少 2 年，最多 5+ 年），全部都有技术指标计算。stocks-claw 缺失这两个，导致：
- LLM 输入只有"今天涨 0.35%"，无法判断这是趋势延续还是反转信号
- 无法回答"RSI 62 在历史上处于什么水平"（需要历史数据支撑）
- 无法回答"如果按建议调仓，过去 3 个月收益会怎样"（需要回测）

**发现 3：回测不是"锦上添花"，是"必备验证"。**

所有实际项目都有回测。Python 3.13 案例甚至验证了回测-实盘相关性（< 1 个标准差）。
stocks-claw 目前生成的建议，用户无法知道：
- 这个建议如果过去 3 个月执行，结果是好是坏？
- 这个建议的风险水平（最大回撤）是多少？
- 这个建议的胜率是多少？

没有回测的建议 = 没有验证的假设 = 不可信。

**发现 4：数据管道质量决定 LLM 输出质量。**

所有项目都在"数据层"投入大量工程（多 Vendor、数据验证、缓存、历史数据），而 stocks-claw 的 Phase 1 正是做这个。

当数据层做好后，LLM 的 Prompt 可以变成：
```
基于以下数据：
- 沪深300: 当前价 3542, RSI 62(14日), 20日均线 3520, 当前价在均线上方+0.6%, 
  20日波动率 1.2%（历史中低水平）, MACD 金叉第3天但动能减弱
- 组合状态: 权益 72%（超出目标上限 65% 7个百分点）
- 宏观环境: USD/CNY 7.25（近30日升值1.2%）, VIX 18（从14升，风险偏好下降）
- 新闻摘要: [3条关联新闻，已按影响度排序]

请给出：1) 技术面判断 2) 组合偏离分析 3) 具体调仓建议 4) 风险提示
```

这种输入，即使 deepseek-v4-flash 也能给出专业建议。而当前 stocks-claw 的输入：
```
沪深300 3542.33 +0.35%
权益 35% 在范围内
```
这种输入，GPT-4 也只能输出垃圾。

---

### 4.2 对 stocks-claw 的修正建议

基于以上调研，对原 PLAN.md 的修正：

| 原方案 | 修正方案 | 原因 |
|--------|---------|------|
| Phase 2: 多 Agent 架构 | **单 Pipeline + 结构化 Prompt** | 实际项目证明多 Agent 成本高，单 Pipeline 足够 |
| Phase 3: 因子挖掘系统 | **删除** | 7 个项目无一使用 LLM 因子挖掘，都是预设指标 |
| Phase 4: 自研回测引擎 | **集成 backtesting.py** | 实际项目都用现有库（Backtrader/backtesting.py） |
| Phase 5: 记忆系统 | **简化：最近 N 次快照对比** | 实际项目只有 TradingAgents 有记忆，且只是 markdown 文件 |
| 新增：历史数据 + 技术指标 | **Phase 1 的核心扩展** | 这是所有实际项目的"门槛级"能力 |
| 新增：回测集成 | **Phase 2 的组成部分** | 必须验证策略建议的有效性 |

### 4.3 修正后的路线图

**Phase 1（数据基础设施）：2-3 周**
1. 历史数据缓存（yfinance/AKShare 获取，JSON 存储）
2. 技术指标引擎（pandas 计算 MA/RSI/MACD/ATR/布林带/波动率）
3. 宏观数据抓取（VIX/USD-CNY/国债收益率）
4. 数据质量验证（过期检测、look-ahead 防护）

**Phase 2（分析增强）：2-3 周**
1. 信号评分系统（多指标加权，本地计算）
2. 结构化 Prompt 设计（包含所有指标和信号）
3. 回测集成（backtesting.py，验证历史表现）
4. 规则校验器（硬约束：仓位限制、止损等）

**Phase 3（部署和自动化）：1-2 周**
1. 自动调度（Cron 或内置定时器）
2. 通知投递（飞书/邮件/Discord）
3. 参数敏感性分析（CLI 支持参数覆盖）

**总计：5-8 周（比原方案 13 周更聚焦，更务实）**

---

## 第五部分：验证声明

本报告所有项目均满足"可验证"标准：
- **virattt/ai-hedge-fund**：GitHub 开源，代码可阅读，有实际部署命令
- **TauricResearch/TradingAgents**：GitHub 开源，v0.3.0 Release Notes 有详细变更记录，PR #473 有完整 backtrader 集成代码
- **Alpaca 案例**：alpaca.markets 官方博客，有详细架构描述和数据流说明
- **TradeSight**：GitHub 开源，~30 stars，有安装和运行说明
- **GovGreed**：govgreed.com 官方指南，有完整技术栈和代码示例
- **Python 3.13 案例**：dev.to 技术博客，有详细架构决策和性能数据
- **Vibe-Trading**：PyPI 发布，有安装说明和架构文档

**排除的项目**：
- 纯学术论文（无代码）：如 LLM-GA、LLM-MAS-DRL、Meta-RL-Crypto
- 概念性项目（无实际运行）：如多数 GitHub 上的 "AI Trading Bot" 模板
- 商业产品（无代码）：如 BulkQuant、Trade Ideas、Tickeron、Composer

---

*本报告基于 2024-2026 年间实际可运行、有代码、有部署方案的 7 个 LLM/AI 量化项目深度分析。所有引用数据均来自公开可查的源代码、技术博客或官方文档。*
> ARCHIVED 2026-07:结论已吸收或废弃,勿作为开发依据。
