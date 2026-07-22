# stocks-claw 开发主线计划

> 版本：v5.0（2026-07-22）
> 北极星：`stocks/VISION.md`。当前实现：`ARCHITECTURE.md`。唯一行动清单：`EXECUTION_PLAN.md`。

## 1. 当前决策

2026-07-22 用户重新确认产品目标：stocks-claw 应由确定性系统整理个人资产、新闻、行情、历史、宏观和数据质量，由 LLM 作为当班私人投资分析师完成综合判断，给出持仓操作、市场方向评估和板块/品类机会；规则系统负责证据和硬约束，不应替代 LLM 成为最终投资决策者。

该裁决与 `VISION.md` 原始定位一致，并取代以下未来方向：

- 继续加厚规则引擎以覆盖所有投资冲突；
- 将受限 `structured_outlook` 视为完整 LLM 分析路径；
- 让 `portfolio_adjudicator` 独占最终建议权；
- 在 push 层通过裸数字集合重新验证 Outlook 语义。

现有生产链继续运行，直到新 Advisory 路径通过影子验收和用户价值验收。不得一次性切换。

## 2. 当前真实状态

### 2.1 已成熟或可复用

- Account / Position v2、持仓成本、币种、分类、流动性和确认式写入；
- A 股、美股、基金、加密等当前价格与历史价格 Provider、主备降级和缓存；
- 新闻、RSS、GNews、SEC EDGAR、巨潮、宏观和未来事件；
- 新闻情报 LLM 聚类、情报归档和信号跟踪；
- 技术指标、轮动、组合暴露、数据异常和数据质量；
- 定时 session、Cron、Artifact、Feishu 投递；
- Advice / Execution / Forecast / Shadow Account 基础台账；
- 1124 项全量测试通过的工程基线（2026-07-22）。

### 2.2 当前结构性偏差

- 最终交易动作主要由 `QuantActionEngine → factor_rules → portfolio_adjudicator` 决定；
- LLM 只负责新闻情报和禁止交易指令的中长期 Outlook；
- 情报采集与组合上下文仍是两条部分分离的路径；
- 旧 `LLMAnalysis` 已废弃，但新的完整结构化 Advisory 尚未建立；
- push 校验混合了时效、格式、完整性和 Outlook 语义，产生双层契约漂移；
- 自然语言资产输入仍依赖外部 Agent 组织写接口，不是正式的 diff/确认工作流；
- 执行反馈和预测复盘存在底座，但未成为最终 LLM 建议的稳定闭环。

## 3. 目标职责边界

```text
Financial Memory          用户确认事实
Unified Harvester         一次采集、同一 as_of
Feature Layer             指标、风险、约束、候选信号
LLM Investment Analyst    综合判断与建议
Advisory Validator        证据和可执行性检查
Presentation              纯投影和格式
Delivery                  时效、版本、投递
User Feedback             执行与复盘
```

规则信号是 `candidate evidence`，不是最终动作。Validator 可以拒绝或要求修正不合法建议，但不能静默改写成另一套投资策略。

## 4. 迁移路线

### A0：冻结目标契约与现状基线

定义 `UnifiedAnalysisSnapshot`、`InvestmentAdvisory`、`AdvisoryValidationReceipt`；为当前生产路径建立可重复基线和失败分类。只新增影子能力，不改变推送。

### A1：自然语言金融记忆入口

实现自然语言资产/画像提取、现有记录 diff、不确定项和用户确认后的原子写入。保持所有长期记忆写入必须确认。

### A2：统一证据快照

合并交易 session 与情报路径的采集结果，统一 `as_of`、来源注册表和 data quality；避免同一窗口重复调用 API 和使用 stale intelligence。

### A3：完整 LLM Advisory 影子路径

LLM 一次读取统一快照，输出市场判断、持仓动作、组合影响、情景、预测候选、板块和品类机会。规则引擎作为输入证据。新路径只保存 shadow artifact，不推送。

### A4：语义与可执行性验证

建立 typed evidence refs、validation receipt、内容哈希和一次反馈重试；检查标的、持仓关系、比例、现金、流动性、结算、风险状态和数据异常。删除跨语义裸数字授权。

### A5：生产报告迁移

先在四个主窗口切换，观察窗口继续 Delta；通过双轨对照后逐步替代现有 `portfolio_decision.user_view` 主导的报告。保留快速回退开关。

### A6：执行反馈与校准

将执行、部分执行、拒绝、延后、预测结算和用户反馈稳定注入后续分析；设定最小样本门槛，禁止少量样本自动改写策略。

## 5. 阶段验收原则

每阶段必须同时通过：

1. **工程闸**：ruff、pytest、compileall、schema 和离线 smoke；
2. **数据闸**：来源、时效、缺失和异常可见；
3. **一致性闸**：职责边界无重复权威；
4. **影子闸**：新旧结果可比较、可回放；
5. **用户价值闸**：用户确认新能力减少决策成本后才进入下一阶段或生产切换。

## 6. 当前不做

- 自动交易和券商下单；
- 用 Prompt 替代结构化契约和验证器；
- 一次性重写全部 Provider、持仓、历史或 Cron；
- 在 A3 影子验收前停用当前生产报告；
- 让 LLM 自由计算金额、持仓事实或来源；
- 为兼容旧产物无限保留多套生产契约。

## 7. 文档权威层级

- `stocks/VISION.md`：产品北极星；
- `PLAN.md`：当前状态、裁决和迁移路线；
- `EXECUTION_PLAN.md`：唯一可执行任务清单；
- `ARCHITECTURE.md`：当前与目标架构；
- `stocks/DATA_MODEL.md`：当前 schema 与计划中的新契约；
- `AGENT_GUIDE.md`：Agent 和开发者操作规则；
- `docs/archive/`：历史证据，无现行效力。

## 8. 决策日志

- 2026-07-03：产品定位升级为个人投资分析师系统；确定性系统是工作台，LLM 是当班分析师，用户是唯一决策人。
- 2026-07-15：对抗性审查确认风险监控与研究底座可用，但规则动作和组合资金部署未通过直接执行验收。
- 2026-07-22：确认系统应回到“统一证据 + LLM 综合分析 + 硬约束验证”的原始愿景；下一主线为重建分析师决策中枢，现有数据底座保留，生产路径采用影子运行和分阶段迁移。
