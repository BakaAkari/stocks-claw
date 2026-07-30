> **ARCHIVED 2026-07-29：历史执行清单快照，无现行实施效力。**
> 当前状态见 `STATUS.md`；长期方向见 `ROADMAP.md`；下一个可执行任务见
> `docs/tasks/`。本文件保留 v6.0（2026-07-22）原文，仅供历史参照——其中的
> 勾选状态、"当前闸门"等表述均已过时，不代表当前实现进度。

# EXECUTION_PLAN.md — LLM 私人投资分析师迁移计划（v6.0 存档）

> 版本：v6.0（2026-07-22）
> 本文是唯一行动清单。实施前必须读取 `stocks/VISION.md`、`PLAN.md`、`ARCHITECTURE.md` 和本任务。

## 全局约束

- 不自动下单，不承诺收益。
- 长期金融记忆写入必须用户确认。
- 当前生产报告在 A5 切换前继续运行；新路径默认 shadow-only。
- 规则引擎输出是 evidence/candidate，不是最终建议权威。
- LLM 不得修改用户事实、行情和确定性计算。
- Validator 只检查证据与可执行性，不生成替代投资策略。
- 任何 schema 变更必须同步代码、`stocks/DATA_MODEL.md` 和 schema 测试。
- 每个任务采用 TDD，单独 commit + push；默认测试不得访问真实网络。

## A0 — 契约冻结与影子基线（当前闸门）

**目标：** 在不改变生产推送的前提下，定义新决策中枢的稳定接口和对照基线。

### A0-1：定义 UnifiedAnalysisSnapshot v1

**计划文件：**
- 新建 `stocks/domain/advisory_models.py`
- 新建 `stocks/engine/unified_snapshot.py`
- 新建 `tests/engine/test_unified_snapshot.py`
- 更新 `stocks/DATA_MODEL.md`

**契约：**
- `snapshot_id`、`generated_at`、`trigger`、`session`、`market_scope`；
- `portfolio`、`profile`、`quotes`、`history_features`、`technical_evidence`；
- `news_clusters`、`filings`、`macro`、`upcoming_events`、`rotation`；
- `portfolio_constraints`、`risk_context`、`candidate_signals`；
- `data_quality`、`source_registry`；
- 每个事实拥有 `fact_id`、`as_of`、`source_ref` 和必要的 `metric/unit`。

**验收：**
- [ ] 同一输入生成稳定 snapshot hash；
- [ ] 不包含 position_id 之外的秘密或 API key；
- [ ] 缺失字段显式标注，不以空值伪装正常；
- [ ] 当前 AnalysisContext 可以无损投影到 snapshot 的第一版子集。

### A0-2：定义 InvestmentAdvisory v1

**计划文件：**
- 修改 `stocks/domain/advisory_models.py`
- 新建 `stocks/engine/advisory_contract.py`
- 新建 `tests/engine/test_advisory_contract.py`

**顶层字段：**
- `market_assessment`
- `portfolio_assessment`
- `actions[]`
- `hold_decisions[]`
- `do_not_do[]`
- `sector_opportunities[]`
- `asset_class_opportunities[]`
- `watchlist_candidates[]`
- `scenarios`
- `forecast_candidates[]`
- `next_checkpoints[]`
- `data_limitations[]`

每个 action 必须有目标、动作、size 语义、理由、evidence refs、执行条件、取消条件、期限和置信度。

**验收：**
- [ ] 不允许自由货币金额；金额由确定性系统计算；
- [ ] action target 必须是持仓、授权候选或组合 bucket；
- [ ] evidence refs 必须能回指 snapshot；
- [ ] 研究候选不能进入已执行动作；
- [ ] forecast candidate 固定 requires_confirmation=true。

### A0-3：建立当前生产基线和对照工具

**计划文件：**
- 新建 `stocks/engine/advisory_shadow_store.py`
- 新建 `scripts/compare_advisory_paths.py`
- 新建 `tests/engine/test_advisory_shadow_store.py`

**验收：**
- [ ] 保存当前规则动作、当前 Outlook、新 Advisory 的同窗对照；
- [ ] 记录差异但不推送、不写 advice/execution/forecast；
- [ ] 支持按 run_id 回放；
- [ ] 生产路径输出字节不变。

### A0 出口

- [ ] 新契约通过代码审查和全量测试；
- [ ] 至少 4 个主窗口 artifact 能转换为 snapshot；
- [ ] 用户审核 advisory schema，确认字段足以表达目标报告；
- [ ] 未改变任何生产推送。

## A1 — 自然语言资产与画像入口

**目标：** 用户直接描述资产和偏好，系统生成可审查 diff，确认后写入。

### A1-1：AssetIntakeDraft

**计划文件：**
- 新建 `stocks/engine/asset_intake.py`
- 新建 `stocks/prompts/asset_intake_prompt.txt`
- 新建 `tests/engine/test_asset_intake.py`

**输出：** `accounts_to_add`、`positions_to_add`、`positions_to_update`、`positions_to_remove`、`profile_updates`、`ambiguities`、`source_quotes`。

### A1-2：确定性 diff 和确认令牌

- 仅系统计算 diff；LLM 不决定覆盖顺序；
- 删除和大额变更显式分组；
- 确认令牌绑定 draft hash 和当前资产文件 hash；
- 文件变化后旧令牌失效。

### A1-3：原子写入和回读验证

- 用户确认后原子更新 v2 文件；
- 写后重新加载并输出实际 diff；
- 未确认、歧义未解决或 hash 变化不得写入。

### A1 出口

- [ ] 真实脱敏资产描述完成一次 preview → confirm → readback；
- [ ] 不确定基金代码、账户、币种和锁定属性会被询问；
- [ ] 没有未经确认的长期记忆写入。

## A2 — 统一采集与证据快照

**目标：** 交易报告和情报分析消费同一时间点的数据，不重复抓取和拼接 stale 快照。

### A2-1：采集计划与缓存键

- 定义 news/quotes/history/macro/filings/events 的并行采集计划；
- 以市场、session、as_of 和 TTL 形成缓存键；
- 相同窗口只发起一次相同 Provider 请求。

### A2-2：统一 source registry

每个来源记录 `source_id`、provider、endpoint 类型、as_of、freshness、status、fallback_chain 和错误分类。

### A2-3：统一情报与组合上下文

- `LLMIntelligenceAnalyzer` 消费本次 snapshot 的新闻部分；
- 交易分析消费同一 snapshot 的 clusters；
- 删除“上个整点 digest 无条件注入当前交易窗口”的路径；
- 独立情报巡逻仍可持续采存，但报告只接受 freshness 合格数据。

### A2 出口

- [ ] 一个主窗口只构建一个 snapshot；
- [ ] API 重复调用量有可复现下降；
- [ ] 新闻、行情、宏观和组合的 as_of 可审计；
- [ ] 现有 Provider、缓存和降级测试全部保留。

## A3 — 完整 LLM Investment Advisory（shadow-only）

**目标：** 让 LLM 基于统一证据完成综合投资判断，但不影响生产。

### A3-1：AdvisoryEvidenceSelector

按高权重持仓、风险持仓、候选动作、组合冲突、重要事件、轮动前列和数据边界选择证据，禁止把完整大 artifact 原样发送给 LLM。

### A3-2：InvestmentAdvisorySynthesizer

**计划文件：**
- 新建 `stocks/engine/advisory_synthesizer.py`
- 新建 `stocks/prompts/investment_advisory_prompt.txt`
- 新建 `tests/engine/test_advisory_synthesizer.py`

LLM 必须逐条说明对候选规则信号的 `adopt / modify / reject / defer`，并处理用户风格、新闻、技术、组合和风险冲突。

### A3-3：影子运行

- 四个主窗口生成 shadow Advisory；
- 不进入 `portfolio_decision.user_view`；
- 不推送；
- 保存模型、prompt、snapshot 和输出 hash。

### A3 出口

- [ ] 连续至少 5 个交易日主窗口影子运行；
- [ ] JSON 成功率、重试率和延迟可统计；
- [ ] 人工抽检无编造事实、无越权金额、无错误标的；
- [ ] 用户确认综合判断比现行拼接报告更接近需求。

## A4 — 语义、风险与可执行性验证

**目标：** 保证 LLM 建议有证据且可执行，同时避免第二套决策逻辑。

### A4-1：Typed Fact / Claim refs

高风险字段先结构化：action size、价格阈值、scenario validation/invalidation、forecast level/deadline、宏观阈值。

### A4-2：AdvisoryValidator

检查：
- evidence ref、metric、unit、source 和 as_of；
- 持仓关系、目标合法性和 action 语义；
- 比例、组合上限、即时现金和结算；
- 锁定、开放期、数据异常和风险暂停；
- 研究候选与今日动作分离。

验证器只能返回 errors/warnings 和允许的确定性派生金额，不得更换动作方向。

### A4-3：一次修正重试与 validation receipt

Receipt 包含 schema、validator、prompt contract、snapshot hash、advisory content hash 和 validated_at。失败反馈给 LLM 修正一次；仍失败则 `review_required`。

### A4-4：重构 push 边界

- Push 只校验 receipt、内容完整性、版本、时效、内部 token 和格式；
- 删除 Outlook/Advisory 的跨语义裸数字集合授权；
- 交易卡数字继续与已验证 action/派生金额精确对应；
- 错误分类和去重。

### A4 出口

- [ ] 相同数值不同 metric 不能互相授权；
- [ ] 内容被修改后 hash 校验失败；
- [ ] 合法 Advisory 不因 payload 中缺少同值数字被误杀；
- [ ] 非法金额、锁定资产和不存在标的仍被拒绝。

## A5 — 生产报告迁移

**目标：** 经影子和验证验收后，用 Advisory 驱动主窗口报告。

### A5-1：确定性 Advisory renderer

输出：本窗口判断、今日动作、持仓决策、组合影响、市场情景、机会候选、数据边界和下一检查点。Renderer 不新增投资观点。

### A5-2：双轨和回退

- 配置 `report_mode=current|advisory_shadow|advisory_primary`；
- A 股盘前/盘后先切换，再切美股；
- observation window 基于已验证 Advisory 做 Delta；
- 当前路径保留一个稳定版本作为短期回退，验收后删除多余兼容层。

### A5-3：用户价值验收

至少连续 10 个交易日对照：建议清晰度、冲突处理、误报、漏报、推送成功率、用户执行/拒绝和主观价值。

### A5 出口

- [ ] 四个主窗口稳定使用 Advisory；
- [ ] Watch Window 无变化 SILENT；
- [ ] 推送成功率和失败分类满足运营标准；
- [ ] 用户明确批准停用旧规则主导报告。

## A6 — 执行、预测与反馈校准

- 将最终 Advisory action 写成用户可确认的 AdviceRecord 草稿；
- 执行/部分执行/拒绝/延后与 action_id 精确关联；
- Forecast candidate 用户确认后进入台账并按期结算；
- 后续 Advisory 读取版本化反馈摘要；
- 样本不足时只展示事实，不自动调参；
- 规则、LLM 建议和用户决策分别归因。

## 每阶段全局验收

```bash
.venv/bin/ruff check .
.venv/bin/pytest -q -o 'addopts='
.venv/bin/python -m compileall -q stocks tests
.venv/bin/python -m stocks.adapters.cli --output json --no-news --no-quotes
```

任何阶段出现生产推送回归、金融记忆越权写入或数据来源丢失，立即回退该阶段，不得通过放宽全局安全检查掩盖。
