# stocks-claw 架构

> 参考文档，描述形状而非动态进度；进度只在 `STATUS.md`。每个组件标注生命周期标签
> `[PRODUCTION]` / `[SHADOW]` / `[PLANNED]` / `[DEPRECATED]`（定义见
> `docs/contracts/README.md`）。本文不记录测试通过数、pytest 计数或"已完成"之类
> 的动态验证结果——那些只属于 `STATUS.md`。数据契约的权威生命周期索引是
> `docs/contracts/README.md`；本文只在架构组件粒度给出导览，两者冲突时以
> `docs/contracts/README.md`（数据契约）和 `STATUS.md`（当前进度）为准。

## 1. 系统边界

stocks-claw 是单用户个人投资分析师系统，不自动下单。

当前生产仍由确定性规则生成大部分动作，LLM 负责情报和受限 Outlook。已批准的目标
方向（见 `ROADMAP.md`）是保留数据底盘，重建以 LLM InvestmentAdvisory 为核心的
决策中枢，并通过 shadow-only 渐进迁移；迁移是否已推进到某一步，只看下表标签和
`STATUS.md`，不看本文措辞。

## 2. 组件标签导览

| 组件 | 标签 | 说明 |
|---|---|---|
| Financial Memory（Account/Position v2） | `[PRODUCTION]` | 用户确认事实来源 |
| `StocksEngine.build_context()` / `AnalysisContext` v12 | `[PRODUCTION]` | 唯一 Agent 输入 |
| Technical / Rotation / QuantAction / Factor Rules | `[PRODUCTION]` | 规则候选与约束，仍兼具部分最终裁决职责（见 §2.2 当前偏差） |
| Portfolio Adjudicator → `portfolio_decision.user_view` | `[PRODUCTION]` | 当前唯一用户可见决策结构 |
| LLM Intelligence Analyzer（新闻情报聚类） | `[PRODUCTION]` | 不产出交易指令 |
| Outlook Synthesizer（受限中长期研判） | `[PRODUCTION]` | `structured_outlook` / `outlook_delta`，禁止交易指令 |
| Deterministic Renderer + Push Validator | `[PRODUCTION]` | `build_push_payload.py` |
| Unified Harvester（统一情报+组合采集） | `[PLANNED]` | 目标：合并当前两条独立采集路径为一次同 `as_of`（ROADMAP A2）；今天仍是两条部分独立路径 |
| `UnifiedAnalysisSnapshot` | `[SHADOW]` | `stocks/domain/advisory_models.py`、`stocks/engine/unified_snapshot.py`；仅 `scripts/run_shadow_advisory.py` 消费 |
| LLM Investment Analyst / `InvestmentAdvisory` | `[SHADOW]` | `advisory_synthesizer.py` + `advisory_contract.py` |
| `AdvisoryValidationReceipt` 校验逻辑 | `[SHADOW]` | 已内置于 `advisory_contract.py`；产出 receipt，供 shadow 对照 |
| 独立 Advisory Validator 模块（含单次修正重试） | `[PLANNED]` | **`advisory_validator.py` 目前不存在**——不要假设有这个文件；ROADMAP A4 描述的完整校验/重试流程尚未作为独立模块落地 |
| `AssetIntakeDraft`（自然语言资产入口，library-only） | `[SHADOW]` | `asset_intake_parser.py`、`llm_asset_intake.py`、`asset_intake_writer.py` 已存在，但只有单测覆盖，无 CLI/MCP adapter 入口 |
| `DecisionEnvelope` | `[DEPRECATED]` | 无生产/shadow 消费者；字段细节见 `docs/contracts/legacy-reference.md` |

`report_mode`（`advisory_shadow` / `advisory_primary` 配置开关）目前**不存在**于
代码中——不要在实现或文档中假设有这个开关；见 `STATUS.md` "Report mode"。

## 3. 当前生产责任链 `[PRODUCTION]`

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

整条链路当前均为 `[PRODUCTION]`。Advisory shadow 链路（§4）不参与其中任何一步。

### 3.1 当前优点

- 多资产金融记忆、持仓成本、流动性和用户画像；
- 多源行情、历史、新闻、公告、宏观、事件和降级；
- 数据质量、异常阻断、技术特征、轮动和组合约束；
- 定时产物、风险状态、执行/预测台账和 Feishu 投递；
- LLM 情报聚类与受限 Outlook。

### 3.2 当前偏差（结构性描述，非任务清单）

- 规则系统兼具证据、决策和动作裁决，职责过重；
- LLM 不拥有完整综合建议权；
- 情报与组合采集路径未完全统一（目标 `[PLANNED]`，见 Unified Harvester）；
- `scheduled_analysis.py`、`build_push_payload.py` 等文件职责过宽；
- 用户自然语言资产输入没有正式 diff/确认模型（library 已是 `[SHADOW]`，adapter
  入口仍是 `[PLANNED]`）。

哪些偏差已经开始被下一步任务处理，见 `docs/tasks/` 的当前任务；本节只描述形状。

## 4. 目标架构

```text
┌────────────────────────────────┐
│ Financial Memory      [PRODUCTION] │
│ accounts / positions / style   │
└────────────────┬────────────────┘
                 │
┌────────────────▼────────────────┐
│ Unified Harvester       [PLANNED] │
│ news, filings, quotes,           │
│ history, macro, events           │
└────────────────┬────────────────┘
                 │
┌────────────────▼────────────────┐
│ UnifiedAnalysisSnapshot [SHADOW] │
│ facts + source registry + DQ    │
└────────────────┬────────────────┘
                 │
┌────────────────▼────────────────┐
│ Feature / Constraint Layer      │
│ [PRODUCTION, 语义未统一为        │
│  evidence/candidate/constraint] │
└────────────────┬────────────────┘
                 │
┌────────────────▼────────────────┐
│ LLM Investment Analyst  [SHADOW] │
│ InvestmentAdvisory              │
└────────────────┬────────────────┘
                 │
┌────────────────▼────────────────┐
│ Advisory Validator      [PLANNED] │
│ receipt 逻辑今在 advisory_contract.py │
│ [SHADOW]；独立模块与重试未落地   │
└────────────────┬────────────────┘
                 │
┌────────────────▼────────────────┐
│ Presentation / Delivery         │
│ [PRODUCTION，尚未接 Advisory]   │
└────────────────┬────────────────┘
                 ▼
               User
                 │
                 ▼
      execution / rejection / review
```

## 5. 目标组件说明

### 5.1 Financial Memory 与 Asset Intake `[PRODUCTION]` + `[SHADOW，library-only]`

`Account`、`Position` 和 investor profile 继续作为事实权威（`[PRODUCTION]`）。
`AssetIntakeDraft`（`[SHADOW，library-only]`）只保存 LLM 提取草稿、diff、不确定项
和 source quote；确认后由确定性写入器原子更新。用户可见的 CLI/MCP 入口仍是
`[PLANNED]`。

### 5.2 Unified Harvester `[PLANNED]`

复用现有 Provider 和缓存，以一次并行采集构建同窗数据。独立情报巡逻可以提前采集，
但主报告必须以 freshness 合格的本次 snapshot 为准。今天尚不存在这一层——现状是
两条部分独立的采集路径（见 §2.2）。

### 5.3 UnifiedAnalysisSnapshot `[SHADOW]`

是 LLM 分析的唯一证据入口。它包含组合、画像、行情、历史特征、情报、宏观、事件、
约束、风险、候选信号、数据质量和 source registry。每个高风险事实使用 `fact_id`、
`metric`、`unit`、`as_of` 和 `source_ref`。已实现并产出真实 shadow 产物，未接入
生产。

### 5.4 Feature / Constraint Layer `[PRODUCTION]`（语义待统一）

保留 `action_signals`、`quant_action`、`factor_rules`、数据异常、组合暴露和流动性；
今天仍直接代表部分最终用户动作，尚未统一输出语义为 evidence/candidate/constraint。

### 5.5 LLM Investment Analyst `[SHADOW]`

一次综合完整证据，输出结构化 `InvestmentAdvisory`。已实现并产出真实 shadow 产物
（`advisory_synthesizer.py`），未接入生产 push 或 `user_view`。

### 5.6 Advisory Validator `[PLANNED]`（receipt 部分 `[SHADOW]`）

目标：唯一语义与可执行性边界，检查 evidence refs、单位、标的、持仓、比例、现金、
锁定、开放期、结算、风险暂停和数据异常，返回问题或确定性派生金额，不改写投资
方向；校验成功生成 receipt（schema version、validator version、prompt contract
hash、snapshot hash、advisory content hash、时间）。

**当前实际状态**：receipt 的字段与哈希逻辑已实现于 `advisory_contract.py`
（`[SHADOW]`），供 shadow 对照使用；但目标描述的"单次修正重试"和独立
`advisory_validator.py` 模块尚未创建——不要在代码或文档中假设它存在。

### 5.7 Presentation 与 Delivery `[PRODUCTION]`（未接 Advisory）

Presentation 只将已验证内容投影为用户文本；Delivery 只检查 artifact 新鲜度、
版本、receipt、内容哈希、内部 token 和渠道格式。当前只投影
`portfolio_decision.user_view`，尚未接入 Advisory 输出；下游不得重新做金融语义
判断。

## 6. 当前代码映射

### 6.1 可直接复用 `[PRODUCTION]`

- `stocks/domain/models.py`
- `stocks/providers/`
- `history_cache.py`、`history_provider.py`、`indicators.py`
- `news_sources.py`、`market_events.py`、`event_calendar.py`
- `macro_data.py`、`exchange_rate.py`
- `intelligence_harvester.py`、`intelligence_analyzer.py`、`news_intelligence_store.py`
- advice / execution / forecast / signal tracker
- scheduled calendar、Artifact store、Cron 和 Feishu 脚本

### 6.2 改变职责（目标方向，`[PRODUCTION]` 今天，职责范围是 `[PLANNED]`）

- `action_signals.py`：最终候选动作 → 技术证据；
- `quant_action.py`：最终动作主来源 → QuantReview；
- `factor_rules.py`：裁决器 → 约束和冲突证据；
- `portfolio_adjudicator.py`：投资决策器 → pre-LLM 组合事实 + post-LLM 可执行性检查；
- `capital_allocation`：部署建议 → 资金事实和计划模拟；
- `presentation.py`：继续纯投影，面向 Advisory 新契约；
- `build_push_payload.py`：移除语义再判断，只保留完整性和渠道边界。

上述均是今天仍在生产运行的现有文件（`[PRODUCTION]`）；"改变职责"这一列描述的是
目标职责边界，本身是 `[PLANNED]`，不代表已经改变。

### 6.3 已存在的 Advisory shadow 文件 `[SHADOW]`

- `advisory_models.py`
- `unified_snapshot.py`
- `asset_intake_parser.py` / `llm_asset_intake.py` / `asset_intake_writer.py`（library-only）
- `advisory_contract.py`（含 `AdvisoryValidationReceipt` 校验逻辑）
- `advisory_synthesizer.py`
- `advisory_shadow_store.py`
- `scripts/run_shadow_advisory.py`、`scripts/compare_advisory_paths.py`
- 对应 prompts 和 tests

### 6.4 尚未创建 `[PLANNED]`

- 独立 `advisory_validator.py` 模块（当前校验逻辑内置于 `advisory_contract.py`）
- `asset_intake` 的 CLI/MCP adapter 入口
- Unified Harvester 本身
- `report_mode` 配置开关（`advisory_shadow` / `advisory_primary`）

## 7. 迁移策略（方向，非进度）

1. 新契约和 shadow store；
2. 自然语言资产入口；
3. Unified Snapshot；
4. Advisory shadow；
5. Validator/receipt；
6. 主窗口双轨切换；
7. 观察窗口 Delta；
8. 用户反馈闭环；
9. 删除不再使用的旧生产兼容层。

每一步是否已经开始或完成，见 §2 标签表和 `STATUS.md`；本节顺序不代表进度。生产
切换前必须保留当前路径，且新路径不得写金融记忆或推送。

## 8. 数据与失败语义（目标方向）

- 缺失、过期、单源、fallback 和异常必须机器可读；
- LLM 失败降级到 `advisory_unavailable` 或当前稳定生产路径，不得伪造建议；
- Validator 失败区分 evidence、semantic、feasibility、integrity、delivery；
- 同一错误去重并提供字段路径、fact ref 和阶段；
- 缓存键包含 snapshot、prompt、schema 和 validator contract hash。

这一节描述目标失败语义设计，不代表 §5.6 的 Validator 已经实现全部区分逻辑。

## 9. 当前运行入口 `[PRODUCTION]`

当前 CLI、MCP、HTTP、定时 Artifact 和 no-agent push 入口不变。详细命令见
`README.md` 和 `AGENT_GUIDE.md`。新 Advisory 在 A5 前只作为
`.local/advisory_shadow/` 影子产物（`scripts/run_shadow_advisory.py`）。

## 10. 当前验证证据

不在本文件维护。ruff/pytest/smoke 的最新结果只记录在 `STATUS.md`，避免与本文件
的架构形状描述产生新旧不一致。
