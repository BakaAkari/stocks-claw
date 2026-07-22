# stocks-claw 架构

> 更新：2026-07-22。本文同时说明当前生产实现与已批准的目标架构；两者不得混写为已经完成。

## 1. 系统边界

stocks-claw 是单用户个人投资分析师系统，不自动下单。

当前生产仍由确定性规则生成大部分动作，LLM 负责情报和受限 Outlook。下一阶段将保留数据底盘，重建以 LLM InvestmentAdvisory 为核心的决策中枢，并通过 shadow-only 渐进迁移。

## 2. 当前生产架构

```text
Financial Memory
      +
Quotes / History / News / Macro / Events
      ↓
StocksEngine.build_context()
      ↓
AnalysisContext v12
      ↓
Technical / Rotation / QuantAction / Factor Rules
      ↓
Action Cards → Portfolio Adjudicator → user_view
      ├─ LLM Intelligence Analyzer（新闻情报）
      └─ Outlook Synthesizer（禁止交易指令的中长期研判）
      ↓
Deterministic Renderer + Push Validator
      ↓
Feishu
```

### 2.1 当前优点

- 多资产金融记忆、持仓成本、流动性和用户画像；
- 多源行情、历史、新闻、公告、宏观、事件和降级；
- 数据质量、异常阻断、技术特征、轮动和组合约束；
- 定时产物、风险状态、执行/预测台账和 Feishu 投递；
- LLM 情报聚类与受限 Outlook。

### 2.2 当前偏差

- 规则系统兼具证据、决策和动作裁决，职责过重；
- LLM 不拥有完整综合建议权；
- 情报与组合采集路径未完全统一；
- Push 层曾用裸数字集合重复 Outlook 语义验证；
- `scheduled_analysis.py`、`build_push_payload.py` 等文件职责过宽；
- 用户自然语言资产输入没有正式 diff/确认模型。

## 3. 目标架构

```text
┌──────────────────────────────┐
│ Financial Memory             │
│ accounts / positions / style │
└──────────────┬───────────────┘
               │
┌──────────────▼───────────────┐
│ Unified Harvester            │
│ news, filings, quotes,        │
│ history, macro, events        │
└──────────────┬───────────────┘
               │
┌──────────────▼───────────────┐
│ UnifiedAnalysisSnapshot      │
│ facts + source registry + DQ │
└──────────────┬───────────────┘
               │
┌──────────────▼───────────────┐
│ Feature / Constraint Layer   │
│ technical, rotation, risk,   │
│ candidate signals, liquidity │
└──────────────┬───────────────┘
               │
┌──────────────▼───────────────┐
│ LLM Investment Analyst       │
│ InvestmentAdvisory           │
└──────────────┬───────────────┘
               │
┌──────────────▼───────────────┐
│ Advisory Validator           │
│ evidence + feasibility       │
│ one retry + receipt          │
└──────────────┬───────────────┘
               │
┌──────────────▼───────────────┐
│ Presentation / Delivery      │
│ pure projection + integrity  │
└──────────────┬───────────────┘
               ▼
             User
               │
               ▼
    execution / rejection / review
```

## 4. 目标组件

### 4.1 Financial Memory 与 Asset Intake

`Account`、`Position` 和 investor profile 继续作为事实权威。新增 AssetIntakeDraft，只保存 LLM 提取草稿、diff、不确定项和 source quote；用户确认后由确定性写入器原子更新。

### 4.2 Unified Harvester

复用现有 Provider 和缓存，以一次并行采集构建同窗数据。独立情报巡逻可以提前采集，但主报告必须以 freshness 合格的本次 snapshot 为准。

### 4.3 UnifiedAnalysisSnapshot

是 LLM 分析的唯一证据入口。它包含组合、画像、行情、历史特征、情报、宏观、事件、约束、风险、候选信号、数据质量和 source registry。每个高风险事实使用 fact_id、metric、unit、as_of 和 source_ref。

### 4.4 Feature / Constraint Layer

保留 `action_signals`、`quant_action`、`factor_rules`、数据异常、组合暴露和流动性，但输出语义统一为 evidence/candidate/constraint。它们不再直接代表最终用户动作。

### 4.5 LLM Investment Analyst

一次综合完整证据，输出结构化 InvestmentAdvisory。它必须处理规则候选、情报、宏观、用户风格和组合冲突，逐条说明 adopt/modify/reject/defer。

### 4.6 Advisory Validator

唯一语义与可执行性边界。检查 evidence refs、单位、标的、持仓、比例、现金、锁定、开放期、结算、风险暂停和数据异常。它返回问题或确定性派生金额，不改写投资方向。

校验成功生成 receipt：schema version、validator version、prompt contract hash、snapshot hash、advisory content hash 和时间。

### 4.7 Presentation 与 Delivery

Presentation 只将已验证 Advisory 投影为用户文本。Delivery 只检查 artifact 新鲜度、版本、receipt、内容哈希、内部 token 和渠道格式。下游不得重新做金融语义判断。

## 5. 当前代码映射

### 可直接复用

- `stocks/domain/models.py`
- `stocks/providers/`
- `history_cache.py`、`history_provider.py`、`indicators.py`
- `news_sources.py`、`market_events.py`、`event_calendar.py`
- `macro_data.py`、`exchange_rate.py`
- `intelligence_harvester.py`、`intelligence_analyzer.py`、`news_intelligence_store.py`
- advice / execution / forecast / signal tracker
- scheduled calendar、Artifact store、Cron 和 Feishu 脚本

### 改变职责

- `action_signals.py`：最终候选动作 → 技术证据；
- `quant_action.py`：最终动作主来源 → QuantReview；
- `factor_rules.py`：裁决器 → 约束和冲突证据；
- `portfolio_adjudicator.py`：投资决策器 → pre-LLM 组合事实 + post-LLM 可执行性检查；
- `capital_allocation`：部署建议 → 资金事实和计划模拟；
- `presentation.py`：继续纯投影，面向 Advisory 新契约；
- `build_push_payload.py`：移除语义再判断，只保留完整性和渠道边界。

### 新增

- `advisory_models.py`
- `unified_snapshot.py`
- `asset_intake.py`
- `advisory_contract.py`
- `advisory_synthesizer.py`
- `advisory_validator.py`
- `advisory_shadow_store.py`
- 对应 prompts、tests 和 compare scripts

## 6. 迁移策略

1. 新契约和 shadow store；
2. 自然语言资产入口；
3. Unified Snapshot；
4. Advisory shadow；
5. Validator/receipt；
6. 主窗口双轨切换；
7. 观察窗口 Delta；
8. 用户反馈闭环；
9. 删除不再使用的旧生产兼容层。

生产切换前必须保留当前路径，且新路径不得写金融记忆或推送。

## 7. 数据与失败语义

- 缺失、过期、单源、fallback 和异常必须机器可读；
- LLM 失败降级到 `advisory_unavailable` 或当前稳定生产路径，不得伪造建议；
- Validator 失败区分 evidence、semantic、feasibility、integrity、delivery；
- 同一错误去重并提供字段路径、fact ref 和阶段；
- 缓存键包含 snapshot、prompt、schema 和 validator contract hash。

## 8. 当前运行入口

当前 CLI、MCP、HTTP、定时 Artifact 和 no-agent push 入口不变。详细命令见 `README.md` 和 `AGENT_GUIDE.md`。新 Advisory 在 A5 前只作为 `.local/advisory_shadow/` 影子产物。

## 9. 验证基线

2026-07-22 新鲜基线：

```text
ruff: pass
pytest: 1124 passed
Finnhub / Polygon / SEC EDGAR: live smoke pass
master == origin/master
```

该基线只证明当前工程可运行，不代表目标 Advisory 架构已经实现。
