# Structured Medium- and Long-Term Outlook Design

## 目标

在保持现有交易指令卡可信边界的前提下，为四个主交易窗口增加结构化中长期研判：A股盘前、A股盘后、美股盘前、美股盘后。

研判覆盖未来 `1–2周` 与 `1–3个月`，综合全部登记持仓、技术扫描池、新闻事件聚类、宏观数据、轮动排名、组合暴露和资金状态。开盘观察与收盘前窗口不重复完整研判，仅在相对上一个主窗口出现实质变化时输出变化摘要。

## 已确认的产品决策

- 使用“受约束的情报综合器 + 确定性推送”方案。
- 中长期研判粒度为：组合、主要资产类别、与持仓高度相关的板块。
- 不逐项为全部资产生成长期预测；单项持仓仅在满足任一客观条件时出现：组合权重进入前5、触发当前风险或交易冲突、或其 `exposure_tags` 与有效新闻事件直接匹配。
- 输出采用基准、乐观、风险三情景。
- 每项判断必须包含验证条件和证伪条件。
- 中长期研判不能新增交易动作；交易指令仍只来自 `portfolio_decision.approved_actions`。
- 主窗口输出完整研判，观察窗口只输出实质变化。
- 最终飞书消息继续由 no-agent 确定性脚本渲染。

## 不变的信任边界

1. 只有 `portfolio_decision.approved_actions` 能进入交易指令卡。
2. `structured_outlook` 只能解释环境、配置倾向、风险和关注方向，不得生成买卖指令、比例或金额。
3. 新闻、宏观、技术、轮动和持仓证据必须先经过结构化授权，综合器不得访问未授权自由文本或完整 scheduled artifact。
4. 所有用户可见数字必须存在于授权输入或 `structured_outlook` 的结构化数值字段中。
5. 新闻判断必须携带来源、发布时间或数据截止时间；无法提供来源的内容不得进入用户报告。
6. 事实与推测必须分离。事实来自结构化输入；情景、影响持续期和未来路径明确标为研判。
7. 数据过期、缺失、单源或方向性情报覆盖不足时，必须降低置信度并明确展示边界。
8. `position_id`、`decision_id`、内部哈希、原始异常码、英文枚举和内部提示词不得进入用户正文。
9. 研判不得承诺收益，不得给未经验证的精确目标价或概率。
10. 现有确定性 push payload 和 fail-closed 校验继续生效。

## 总体架构

`资产账本 + 持仓估值 + 技术扫描 + 新闻事件聚类 + 宏观数据 + 轮动 + 组合暴露`

→ `OutlookEvidenceBuilder`（生成最小化、可引用证据包）

→ `ConstrainedOutlookSynthesizer`（只依据证据包生成结构化三情景研判）

→ `OutlookValidator`（来源、数字、时效、完整性、越权动作、内部字段、情景一致性检查）

→ `structured_outlook`

→ `portfolio_decision.user_view.assistant_brief.outlook`

→ `build_push_payload → deterministic render`

## 组件边界

### OutlookEvidenceBuilder

职责：把当前系统已有数据转换成综合器唯一可读取的证据包。

授权输入：

- `position_valuations`
- `portfolio_mapping`、`exposure_summary`、`liquidity_summary`
- `action_signals` 的技术状态和扫描排名
- `rotation.items`
- `intelligence_digest.top_clusters`
- `intelligence_digest.top_signals`
- `intelligence_health`、`intelligence_coverage`
- `market_state`
- `upcoming_events`
- `data_quality`
- `risk_state`
- `portfolio_decision.cash_schedule`

输出 `outlook_evidence`：

- `as_of`
- `portfolio_snapshot`
- `asset_class_snapshot`
- `sector_snapshot`
- `technical_evidence`
- `rotation_evidence`
- `intelligence_events`
- `directional_intelligence`
- `macro_evidence`
- `upcoming_events`
- `risk_context`
- `data_boundaries`

每条新闻事件必须规范为：`event_id`、`theme`、`summary`、`sources[]`（来源名、标题、URL、发布时间）、`urgency`、`sentiment`、`affected_exposures[]`、`affected_positions[]`（只用公开名称与代码）、`fact_statement`、`as_of`。

若事件聚类没有可验证来源，事件不得进入用户可见研判，只能进入内部数据缺口。

### ConstrainedOutlookSynthesizer

职责：把 `outlook_evidence` 转换为严格 JSON，不负责最终语言排版。

实现采用外部 OpenAI-compatible 模型调用，与交易 push 脚本解耦。模型只接收证据包和固定 JSON Schema，不接收完整 scheduled artifact。

必须输出：`summary`、`near_term`、`medium_term`、`asset_views[]`、`sector_views[]`、`scenarios`、`portfolio_implications[]`、`validation_conditions[]`、`invalidation_conditions[]`、`source_refs[]`、`confidence`、`confidence_reasons[]`、`data_limitations[]`。

禁止输出：

- 交易动作、下单比例、交易金额
- 未在证据包出现的标的、数字、来源或事件
- 精确收益率预测
- 无验证条件的单向结论
- 把情景推测写成已发生事实

### OutlookValidator

职责：综合器输出未通过时 fail-closed，不污染交易报告。

校验规则：

- JSON Schema 完整。
- 四个主窗口必须同时有 `near_term` 和 `medium_term`。
- 三情景必须齐全：`base`、`bull`、`risk`。
- 每个情景包含驱动、组合影响、验证条件和证伪条件。
- 所有来源引用必须能在 `outlook_evidence.intelligence_events[].sources[]` 找到。
- 所有公开标的必须存在于持仓或扫描池授权清单。
- 所有用户可见数值必须能追溯到证据包数值字段；时间跨度标签和枚举序号除外。
- 文本不得包含内部字段或机器 ID。
- 不得出现动作型表达：买入、卖出、减仓、加仓、清仓及比例/金额建议。可以使用“偏有利”“偏不利”“提高关注优先级”“配置风险上升”等研判表达。
- 新闻事件距离当前时间超过72小时，不得作为 `1–2周` 高置信驱动；超过14天，不得作为 `1–3个月` 新增事件驱动。仍持续生效的政策或冲突必须由24小时内的新来源重新确认。
- 宏观数据较旧时必须进入 `data_limitations`，不得作为单独的高置信方向依据。
- `directional_intelligence=0` 时，个股或板块方向置信度最高为 `low`；事件仍可用于组合风险情景。

失败策略：

- 主交易指令卡照常生成。
- 中长期研判显示“本期研判未通过数据完整性校验，暂不输出”，并列出非敏感边界原因。
- 不回退到 LLM 自由文本。

### OutlookDelta

职责：避免八窗口重复。

四个主窗口保存完整 `structured_outlook`。四个观察窗口比较最近两个有效主窗口：

- 情景是否改变
- 置信度是否改变
- 新增或消失的关键驱动
- 新增或解除的验证或证伪条件
- 资产类别或板块方向是否改变

只有存在实质变化时生成 `outlook_delta`。无变化不增加报告段落；观察窗口原有 SILENT 规则保持不变。

## structured_outlook 契约

顶层字段：

- `version: 1`
- `generated_at`
- `horizons: ["1-2w", "1-3m"]`
- `summary`
- `near_term`: `horizon`、`base_view`、`key_drivers[]`、`portfolio_effect`
- `medium_term`: `horizon`、`base_view`、`key_drivers[]`、`portfolio_effect`
- `asset_views[]`: `asset_class`、`direction`、`horizon`、`rationale`、`source_ref_ids[]`
- `sector_views[]`: `sector`、`relationship`、`direction`、`horizon`、`rationale`、`validation`、`invalidation`、`source_ref_ids[]`
- `scenarios.base|bull|risk`: `label`、`drivers[]`、`portfolio_effect`、`validation[]`、`invalidation[]`
- `portfolio_implications[]`
- `source_refs[]`: `id`、`source`、`title`、`url`、`published_at`、`fact`
- `confidence: high|medium|low`
- `confidence_reasons[]`
- `data_limitations[]`

## 用户报告布局

现有两层结构不变，研判作为私人投资助理内部的第三部分：

1. **交易指令卡**
2. **私人投资助理**
   - 为什么这样安排
   - 现在不要做什么
   - 资金状态
   - 组合与风险
   - **中长期研判**
     - 核心结论
     - 未来1–2周
     - 未来1–3个月
     - 资产类别与持仓相关板块
     - 基准、乐观、风险情景
     - 验证与证伪条件
     - 新闻与数据来源
     - 数据边界
   - 仅供观察

主窗口最多展示：4个资产类别、3–5个持仓相关板块、每个情景最多3个驱动、最多5条来源引用。

观察窗口只展示 `outlook_delta`，例如：“相较盘前，能源由中性转为偏有利，原因是新增可验证供应中断事件。”无实质变化时不显示。

## 置信度规则

基础置信度由确定性规则预先计算，模型不得自行提高：

- `high`：主市场行情为 `fresh/current`，宏观数据未超过其发布周期，关键新闻在72小时内且至少2个独立来源支持，方向性情报覆盖率至少60%，技术与情报无反向冲突。
- `medium`：只有一项降级因素：主市场行情为前收盘但不超过1个交易日、关键新闻只有单源、方向性情报覆盖率为20%–60%，或技术与情报存在一个可解释分歧。
- `low`：任一硬降级条件成立：主市场行情超过1个交易日、宏观超过其发布周期、方向性情报覆盖率低于20%或信号数为0、关键新闻无可验证来源、或数据异常影响组合权重前5持仓。

综合器可以降低预计算置信度，不得提高。

当前真实 `cn_after_close` 数据中方向性情报信号为0，因此即使存在 critical 地缘事件，板块方向研判最高只能是 `low`；组合风险情景可以引用该事件，但必须明确来源和边界。

## 生成频率和缓存

- 四个主窗口各生成一次完整研判。
- 证据包内容哈希未变化时复用最近有效研判，避免重复模型调用。
- 观察窗口不调用综合模型，只计算确定性 delta。
- 模型或API失败时沿用最近一个仍在有效期内的研判，并明确“沿用上一主窗口”；超出有效期则不输出。
- `1–2周` 研判最长有效期为24小时。
- `1–3个月` 研判最长有效期为7天，但每个主窗口仍检查证据是否发生实质变化。

## 与预测台账的关系

系统已有 `ForecastRecord` 只保存用户明确确认的、可结算预测。本功能不自动把所有情景写入预测台账。

仅当结构化研判中存在明确目标或宏观指标、比较符和阈值、截止日期及证据来源的可验证命题时，生成 `forecast_candidate`。`forecast_candidate` 仍需用户确认后才能保存；不得自动写入持久预测台账。

## 测试策略

严格 TDD，按以下切片推进：

1. 证据包只包含授权字段，并过滤无来源新闻。
2. Schema 校验拒绝缺失情景、来源越权、数字越权、内部 ID 和交易动作。
3. 置信度上限正确处理 `directional_intelligence=0`、宏观过期和单源新闻。
4. 主窗口生成完整 outlook；观察窗口不调用综合模型，只生成 delta。
5. `build_user_view` 只接受已验证的 `structured_outlook`。
6. push payload 仍只读取 `user_view`，确定性渲染新段落。
7. 模型失败、缓存过期和校验失败均不会影响交易指令卡。
8. hostile fixture 验证模型尝试编造数字、来源、动作和标的时会 fail-closed。
9. 真实四主窗口 force-run，逐数字和逐来源核对。
10. 全量 pytest、Ruff、compileall、报告扫描、cron 手动触发与输出回读。

## 验收标准

1. 四个主窗口报告包含 `1–2周` 和 `1–3个月` 两层研判。
2. 每份完整研判包含基准、乐观、风险三情景。
3. 每个场景至少有一个验证条件和一个证伪条件。
4. 每条新闻驱动均可回溯到来源、标题、URL和发布时间。
5. 方向性情报为0时，板块方向置信度不高于低。
6. 宏观数据过期时，报告明确标注，且不得把宏观数据作为高置信单一依据。
7. 中长期研判不出现交易动作、比例或金额，不改变 `approved_actions`。
8. 观察窗口无实质变化时不重复完整研判。
9. 综合器或校验失败时，交易指令卡照常输出，中长期研判安全降级。
10. 最终推送仍为 no-agent 确定性渲染，并通过数字、来源和内部代号扫描。
11. 不使用无客观标准的“重要性阈值”；所有门槛均能通过字段、数量、时效或状态明确判定。
12. 全量自动化测试和真实端到端推送验收通过。
