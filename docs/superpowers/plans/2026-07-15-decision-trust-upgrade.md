# stocks-claw Decision Trust Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 stocks-claw 从风险研究工作台升级为“数据可审计、组合已裁决、可人工确认执行、可记录效果”的决策系统。

**Architecture:** 保留现有 ContextBuilder、QuantActionEngine 和 ScheduledAnalysisRunner，在它们之间加入 PositionEvidence、DataAnomalyGate、CashSchedule、PortfolioAdjudicator、RiskStateStore 和 WindowDelta。Raw Action Card 只读；最终报告只消费 PortfolioDecision。

**Tech Stack:** Python 3.13、dataclasses、pandas、pytest、ruff、现有 JSON Artifact 与 `.local/` 持久化。

## Global Constraints

- 不自动交易，不承诺收益。
- 不新增外部依赖、扫描池、新闻源或指标。
- 默认测试不访问真实网络。
- 所有新状态文件使用临时文件 + `os.replace()` 原子写入。
- 每个任务先 RED、再 GREEN、再真实 Artifact 验收、再独立 reviewer。
- 任一停止条件触发时，不得继续下一个任务。

---

## 文件结构

- Create: `stocks/engine/data_quality_gate.py` — 逐持仓 freshness 与价格异常判定。
- Create: `stocks/engine/portfolio_adjudicator.py` — CashSchedule、冲突裁决、换仓链和执行计划。
- Create: `stocks/engine/risk_state.py` — 风险状态机与原子持久化。
- Create: `stocks/engine/window_delta.py` — 窗口间结构化差异与 SILENT 判断。
- Create: `stocks/engine/outcome_attribution.py` — Decision 版本与后续效果结算。
- Modify: `stocks/engine/context_builder.py` — evidence、逐市场 freshness、异常输入和现金分层。
- Modify: `stocks/engine/quant_action.py` — 数据闸、raw fields、参数语义。
- Modify: `stocks/engine/factor_rules.py` — freshness 消费逐持仓 evidence；统一情报匹配结果。
- Modify: `stocks/engine/scheduled_analysis.py` — 编排新链、报告契约、priority/notification。
- Modify: `stocks/domain/models.py` — ExecutionRecord 扩展和必要模型。
- Modify: `stocks/engine/intelligence_analyzer.py` — provenance 字段。
- Modify: `scripts/intelligence_brief.py` — brief 健康元数据。
- Tests: 新建与扩展 `tests/engine/test_data_quality_gate.py`、`test_portfolio_adjudicator.py`、`test_risk_state.py`、`test_window_delta.py`、`test_outcome_attribution.py` 及现有核心测试。

---

### Task 1: 逐市场与逐持仓 Freshness

**Files:**
- Modify: `stocks/engine/context_builder.py:1193-1303`
- Modify: `stocks/engine/context_builder.py:492-637`
- Modify: `stocks/engine/scheduled_analysis.py:712-763`
- Modify: `stocks/engine/quant_action.py:603-797`
- Modify: `stocks/engine/factor_rules.py:138-151`
- Test: `tests/engine/test_context_builder.py`
- Test: `tests/engine/test_scheduled_analysis.py`
- Test: `tests/engine/test_factor_rules.py`

**Interfaces:**
- Produces `quote_quality.by_market[market].freshness`。
- Produces `position_valuation.evidence.price_freshness` 与 `indicator_freshness`。
- `finalize_decision(..., data_freshness=position_evidence["price_freshness"])`。

- [ ] **Step 1: 写失败测试：跨市场不互相污染**

在 `test_context_builder.py` 新增 A 股当日、US 前收数据，断言全局可为 previous_close/stale，但 `by_market.a.freshness == "current"`、`by_market.us.freshness == "previous_close"`。

- [ ] **Step 2: 写失败测试：Action Card 使用持仓 freshness**

在 `test_scheduled_analysis.py` 构造 A 股 current、US previous_close 的两持仓，断言 A 股 reduce ratio 不乘 0.5，US ratio 按规则降权。

- [ ] **Step 3: 运行 RED**

Run: `uv run pytest -q tests/engine/test_context_builder.py -k freshness tests/engine/test_scheduled_analysis.py -k freshness`
Expected: 新断言失败，证明当前仍使用全局 freshness。

- [ ] **Step 4: 实现 market freshness**

在 `_quote_quality()` 的 `by_market` 中调用 `_freshness_from_datetime(market_oldest_as_of, generated_at)`，归一化为六档枚举。保留顶层 freshness 仅供健康摘要。

- [ ] **Step 5: 实现 PositionEvidence 时间字段**

在 `_value_position()` 返回值中增加 evidence。instrument market 从 `instrument_key` 提取；fund_nav 使用 `t1_confirmed`；有效手工估值使用 `manual_current`；缺时间为 `missing`。

- [ ] **Step 6: 按持仓传 freshness**

`_build_action_cards()` 不再接收一个全局字符串，改为读取每个 item 的 evidence。`DataFreshnessRule` 接受规范枚举：previous_close 只对需要盘中精度的 add/reduce 降权；stale/missing 阻断非硬纪律动作。

- [ ] **Step 7: 运行 GREEN**

Run: `uv run pytest -q tests/engine/test_context_builder.py tests/engine/test_scheduled_analysis.py tests/engine/test_factor_rules.py`
Expected: PASS。

- [ ] **Step 8: 真实验收**

Run: `uv run python -m stocks.adapters.cli --scheduled-run-session cn_pre_close --now "2026-07-15T14:45:00+08:00" --force`
检查 `a_588000` facts 不再出现因美股导致的 freshness ×0.5；US 卡仍标 previous_close。

- [ ] **Step 9: Commit**

`git commit -am "fix: scope freshness by market and position"`

**停止条件:** 任一 A 股 current 持仓仍因 US previous_close 被降权。

---

### Task 2: 数据异常守门

**Files:**
- Create: `stocks/engine/data_quality_gate.py`
- Modify: `stocks/engine/context_builder.py:1619-1624`
- Modify: `stocks/engine/context_builder.py:492-637`
- Modify: `stocks/engine/quant_action.py:603-681`
- Test: `tests/engine/test_data_quality_gate.py`
- Test: `tests/engine/test_scheduled_analysis.py`

**Interfaces:**
- `detect_price_anomalies(frame: pd.DataFrame, *, current_price: float|None, ma20: float|None) -> list[dict]`
- Evidence writes `data_anomalies`, `action_eligible`, `blocked_reasons`。

- [ ] **Step 1: 写半导体真实回归 Fixture**

从 `.local/history/a_512480.json` 抽取 2026-06-25 至 2026-07-15 的最小匿名价格序列到测试常量，包含 `2.70 → 1.33` 跳变。

- [ ] **Step 2: 写失败测试**

断言检测到 `single_bar_jump` 和 `mixed_adjustment_regime`；Action Card 为 hold/suppressed，原始 MA20 reduce 只能保存在 raw fields，不能进入 executable action。

- [ ] **Step 3: 写正常场景测试**

正常 5–10% 波动、连续趋势下跌和除息小跳变不得误报 block。

- [ ] **Step 4: 运行 RED**

Run: `uv run pytest -q tests/engine/test_data_quality_gate.py`
Expected: import/function missing。

- [ ] **Step 5: 实现纯函数检测器**

所有阈值定义为模块常量，并允许 engine config 覆盖；不做模糊评分，只返回可复现 code/severity/evidence。

- [ ] **Step 6: 接入 ContextBuilder**

指标计算时同时把 history 最后 60 根传给检测器，结果进入 position evidence。

- [ ] **Step 7: 接入 FinalDecision 入口**

`action_eligible=false` 时返回 `signal="hold"`、`ratio=0`、`action="数据异常，暂停技术动作"`；保留 raw technical result 供审计。

- [ ] **Step 8: GREEN + 真实验收**

Run: `uv run pytest -q tests/engine/test_data_quality_gate.py tests/engine/test_scheduled_analysis.py`
再强制跑 `cn_pre_close`，断言 `a_512480` 不在 approved actions，且 anomaly code 明确。

- [ ] **Step 9: Commit**

`git commit -am "fix: block technical actions on price regime anomalies"`

**停止条件:** 半导体 ETF 仍输出可执行减仓 50%，或正常 ETF Fixture 被误阻断。

---

### Task 3: 情报健康、Provenance 与统一匹配

**Files:**
- Modify: `stocks/engine/intelligence_analyzer.py`
- Modify: `stocks/engine/context_builder.py:1710-1740`
- Modify: `stocks/engine/factor_rules.py:154-187`
- Modify: `stocks/engine/quant_action.py` (`_build_drivers`, `_detect_dissent`)
- Modify: `scripts/intelligence_brief.py`
- Test: `tests/engine/test_intelligence.py`
- Test: `tests/engine/test_intelligence_driver_coverage.py`
- Test: `tests/engine/test_factor_rules.py`

**Interfaces:**
- Signal adds `generation_method`, `match_method`, `source_as_of`。
- Create one matcher: `match_intelligence(position, signals) -> list[MatchedSignal]`，Driver/Conflict/Dissent 共用。
- Produce `intelligence_health.status` and `age_minutes`。

- [ ] **Step 1: 写失败测试：过期 brief**

latest brief 比 global watch 旧 48 小时时，health=stale，不能升级 Risk State，driver 标 unavailable。

- [ ] **Step 2: 写失败测试：padding 不算方向覆盖**

15/15 字段可有值，但 coverage 必须分解为 field、directional、padding、exact/proxy/category。

- [ ] **Step 3: 写失败测试：统一分歧**

同一 positive event 与 reduce 卡匹配后，Driver 显示 bullish、Conflict 识别 caution、Dissent 非空。

- [ ] **Step 4: RED**

Run: `uv run pytest -q tests/engine/test_intelligence.py tests/engine/test_intelligence_driver_coverage.py tests/engine/test_factor_rules.py`

- [ ] **Step 5: 添加 provenance 字段并贯穿 digest**

LLM=`llm`，规则回退=`rule_fallback`，补齐=`category_padding`。匹配层写 exact/proxy/exposure_tag/category。

- [ ] **Step 6: 单一 Matcher**

删除 Driver 和 IntelConflictRule 的重复匹配分支，统一消费标准化结果。

- [ ] **Step 7: brief 健康守门**

`intelligence_brief.py` 写 `source_run_id/source_generated_at/brief_generated_at`。scheduled run 比较时间，stale 时不把 brief 送入风险升级。

- [ ] **Step 8: GREEN + Artifact 验收**

检查 coverage 六维计数、每个 driver provenance、dissent 与 conflict 一致。

- [ ] **Step 9: Commit**

`git commit -am "fix: make intelligence provenance and matching auditable"`

**停止条件:** category padding 仍被报告为方向覆盖，或 Driver/Conflict/Dissent 对同一证据结论不同。

---

### Task 4: Action Card 不可变与 CashSchedule

**Files:**
- Create: `stocks/engine/portfolio_adjudicator.py`
- Modify: `stocks/engine/context_builder.py:773-803`
- Modify: `stocks/engine/scheduled_analysis.py:1884-2140`
- Test: `tests/engine/test_portfolio_adjudicator.py`
- Test: `tests/engine/test_context_builder.py`

**Interfaces:**
- `build_cash_schedule(position_valuations, approved_sales, total_value) -> dict`
- `_build_capital_allocation()` 临时保留兼容输出，但不得 mutate cards。

- [ ] **Step 1: 写不可变测试**

深拷贝 action_cards，调用 allocation 后断言输入完全相等；低于 ¥800 的 add 在 suppression 输出中出现，原 card 不变。

- [ ] **Step 2: 写现金时序测试**

用真实结构比例 Fixture：cash/T0、ETF、QDII、黄金、理财、保险。断言未卖 ETF/基金进入 strategic_exit，不进入 immediate_cash。

- [ ] **Step 3: RED**

Run: `uv run pytest -q tests/engine/test_portfolio_adjudicator.py`

- [ ] **Step 4: 实现 CashSchedule**

使用 liquidity tier + product_type + approved sale settlement 规则分类。安全垫只从 immediate cash 扣除，不从战略资产扣除。

- [ ] **Step 5: 移除 mutation**

删除 scheduled_analysis.py 中 `card["signal"] = ...` 等原地写入，改为 suppression record。

- [ ] **Step 6: GREEN + 真实数字验收**

强制 session 后，`immediate_cash_cny` 应接近现有 `cash_or_t0`，不再接近 101 万；具体差异必须能按 position_ids 对账。

- [ ] **Step 7: Commit**

`git commit -am "refactor: preserve raw actions and model cash settlement"`

**停止条件:** immediate cash 包含未卖证券，或 action_cards 前后不一致。

---

### Task 5: PortfolioAdjudicator 与换仓链

**Files:**
- Modify: `stocks/engine/portfolio_adjudicator.py`
- Modify: `stocks/engine/scheduled_analysis.py:751-843`
- Test: `tests/engine/test_portfolio_adjudicator.py`
- Test: `tests/engine/test_scheduled_analysis.py`

**Interfaces:**
- `adjudicate_portfolio(raw_cards, evidences, constraints, risk_state, liquidity) -> PortfolioDecision`
- Stable `decision_id = sha256(run_id + position_id + raw_signal + raw_ratio + rule_version)[:16]`。

- [ ] **Step 1: 写六个冲突 Fixture**

覆盖：数据异常、黄金超配加仓、权益低配减仓无替代、权益低配减仓有替代、风险暂停加仓、锁定资产。

- [ ] **Step 2: 写换仓链断言**

有替代腿时输出 sale/buy、结算时间、post-trade ratio；无替代腿 status=review_required 且 approved_actions 不包含该链。

- [ ] **Step 3: RED**

Run: `uv run pytest -q tests/engine/test_portfolio_adjudicator.py`

- [ ] **Step 4: 实现确定性裁决器**

不调用 LLM。approved、suppressed、review_required 互斥；unresolved_conflicts 非空时 status 不得为 approved。

- [ ] **Step 5: 接入 scheduled run**

新增 `portfolio_decision` 顶级字段。capital_allocation 降级为事实与候选输入，不再代表最终动作。

- [ ] **Step 6: 真实 Artifact 验收**

当前权益 16%、黄金 16.7% 场景必须输出明确 review_required 或完整换仓链，不允许“101万关注XBI”。

- [ ] **Step 7: Commit**

`git commit -am "feat: adjudicate portfolio actions before reporting"`

**停止条件:** unresolved_conflicts 非空但 status=approved。

---

### Task 6: 修正参数语义

**Files:**
- Modify: `stocks/engine/profile_interpreter.py`
- Modify: `stocks/engine/quant_action.py:239-337`
- Modify: `stocks/engine/scheduled_analysis.py:1260-1300`
- Modify: `AGENT_GUIDE.md`
- Test: new `tests/engine/test_quant_action.py`
- Test: `tests/engine/test_profile_interpreter.py`

**Interfaces:**
- Rename `trend_confirm_days` → `trend_break_extra_deviation_pct`，默认 0；提供一次兼容迁移。
- Rename `add_ladder` → `ma20_pullback_add_ratios`，明确按 MA20 偏离选档。

- [ ] **Step 1: 写当前行为锁定测试**

证明旧 `trend_confirm_days=3` 实际是额外 1% 偏离，不是连续三日；证明 add ladder 按 MA20 偏离。

- [ ] **Step 2: 写新参数测试**

新名称产生同一行为；旧字段加载时写 migration warning；两字段同时存在时拒绝启动，避免歧义。

- [ ] **Step 3: 实现迁移与文档同步**

computed_profile 读取时迁移，写回新 schema version；Agent 文案禁止写“连续3天确认”。

- [ ] **Step 4: GREEN + Commit**

Run: `uv run pytest -q tests/engine/test_quant_action.py tests/engine/test_profile_interpreter.py`

`git commit -am "fix: align personalized parameter names with behavior"`

**停止条件:** 报告继续声称系统追踪连续 N 天但没有状态数据。

---

### Task 7: 风险状态生命周期

**Files:**
- Create: `stocks/engine/risk_state.py`
- Modify: `stocks/engine/risk_warning.py`
- Modify: `stocks/engine/scheduled_analysis.py:860-886`
- Test: `tests/engine/test_risk_state.py`

**Interfaces:**
- `RiskObservation`：candidate level、independent evidence keys、observed_at、expires_at。
- `RiskStateStore.update(observation) -> RiskState`。

- [ ] **Step 1: 写状态转移表测试**

覆盖 normal→watch、单 cluster 不直升 hedge、双证据升 hedge、连续确认、两轮降级、TTL 自动降级、相同 observation 幂等。

- [ ] **Step 2: 写原子持久化测试**

模拟写入中断，旧状态文件仍可解析；tmp 文件不被读取。

- [ ] **Step 3: RED**

Run: `uv run pytest -q tests/engine/test_risk_state.py`

- [ ] **Step 4: 实现状态机**

默认规则写入 engine config：critical 新闻 6h TTL；hedge 需 2 independent evidence 或 2 consecutive confirmations；deescalation_confirmations=2。

- [ ] **Step 5: 接入两个运行路径**

`build_scheduled_run()` 与 `build_intelligence_run()` 使用同一 RiskStateStore，避免一个 normal、一个 hedge 的分叉。

- [ ] **Step 6: 真实重跑验收**

连续两次相同输入 transition=unchanged；移除单条 cluster 后不得 23 分钟内无理由 hedge→normal。

- [ ] **Step 7: Commit**

`git commit -am "feat: persist risk state with confirmation and ttl"`

**停止条件:** 相同事实输入产生不可解释的风险跳变。

---

### Task 8: Priority、通知与 Window Delta

**Files:**
- Create: `stocks/engine/window_delta.py`
- Modify: `stocks/engine/scheduled_analysis.py:789-843, 2233-2272`
- Modify: `stocks/config/scheduled_sessions.json`
- Test: `tests/engine/test_window_delta.py`
- Test: `tests/engine/test_scheduled_analysis.py`

**Interfaces:**
- `compute_window_delta(previous_run, current_run) -> dict`
- `_priority(risk_state, portfolio_decision, fired_triggers) -> normal|high|critical`

- [ ] **Step 1: 写 Delta 测试**

相同 action/risk/anomaly/trigger → `has_material_change=false`；ratio、状态、异常或 fired trigger 变化 → true。

- [ ] **Step 2: 写 priority 测试**

手工黄金高亏但无 approved urgent action、risk normal → 不得 critical。硬止损获批或 risk hedge escalation → critical。

- [ ] **Step 3: 写 watch SILENT 测试**

open_watch/pre_close 无 material change → archive_only；有新 action/risk transition → 按配置 push。

- [ ] **Step 4: 实现并接入 latest previous artifact**

比较同市场上一窗口，输出 `window_delta`。首次运行标 initial，不静默。

- [ ] **Step 5: 真实跨窗口验收**

连续强制运行相同数据，第二次应 archive_only；改变一个 trigger Fixture 后恢复 push。

- [ ] **Step 6: Commit**

`git commit -am "feat: make scheduled reports delta-driven"`

**停止条件:** risk normal 且无新动作仍 priority=critical，或无变化 watch 仍 push_now。

---

### Task 9: 报告契约重构

**Files:**
- Modify: `stocks/engine/scheduled_analysis.py:1312-1505`
- Modify: Markdown renderer in `stocks/engine/scheduled_analysis.py`
- Modify: `AGENT_GUIDE.md`
- Test: `tests/engine/test_scheduled_analysis.py`

**Interfaces:**
- Agent Task only reads `window_delta`、`portfolio_decision`、`risk_state`、`data_boundaries`、`research_candidates`。

- [ ] **Step 1: 写结构测试**

要求五段、最多 3 approved actions、research_only 不得进入 action section、每个动作有 cancel condition/settlement/next checkpoint。

- [ ] **Step 2: 写 anti-hallucination 测试**

Renderer 中所有金额、比例、事件必须能在 Artifact 结构字段中找到；禁止从 free-text rationale 抽数字作为动作金额。

- [ ] **Step 3: RED**

Run: `uv run pytest -q tests/engine/test_scheduled_analysis.py -k "report or agent_task"`

- [ ] **Step 4: 精简 Agent Task**

删除“必须列全部扫描信号”和“至少三方向”对执行窗口的硬要求；研究区可单独保留，但风险暂停时标记解除后再评估。

- [ ] **Step 5: 真实可读性验收**

生成 cn_pre_close MD；由独立 reviewer 在不看 JSON 的情况下 60 秒内指出今日最多 3 个动作、禁止动作、到账时间和下一检查点。随后逐项与 JSON 对账。

- [ ] **Step 6: Commit**

`git commit -am "refactor: separate executable decisions from research"`

**停止条件:** reviewer 无法在 60 秒内识别唯一行动，或 MD 与 JSON 有事实差异。

---

### Task 10: Decision 与 Execution 反馈闭环

**Files:**
- Modify: `stocks/domain/models.py:1169-1250`
- Modify: `stocks/engine/persistence.py`
- Modify: `stocks/engine/advice_review.py`
- Modify: `stocks/adapters/cli.py`
- Modify: `stocks/engine/scheduled_analysis.py`
- Test: `tests/engine/test_persistence.py`
- Test: `tests/engine/test_advice_loop.py`

**Interfaces:**
- ExecutionRecord adds run_id、decision_id、planned_ratio、executed_ratio、executed_price、status、rejection_reason。
- CLI: existing `--execution-save` accepts new schema; add `--execution-pending RUN_ID`。

- [ ] **Step 1: 写模型验证测试**

executed 必须有价格和 executed_ratio；rejected 必须有 rejection_reason；deferred 可有 next_review_at；decision_id 必填。

- [ ] **Step 2: 写匹配测试**

按 decision_id 精确关联，禁止只靠 target 模糊匹配。下一 scheduled run 输出 execution_review。

- [ ] **Step 3: 迁移旧记录**

旧 ExecutionRecord 读取为 legacy，保留但不进入 Decision 级归因。

- [ ] **Step 4: GREEN + CLI smoke**

保存 executed/rejected/deferred 各一条测试记录，读回并附加到下一 run。

- [ ] **Step 5: Commit**

`git commit -am "feat: link execution feedback to approved decisions"`

---

### Task 11: 版本化效果归因与 Shadow Trial

**Files:**
- Create: `stocks/engine/outcome_attribution.py`
- Modify: `stocks/engine/persistence.py`
- Modify: `stocks/adapters/cli.py`
- Test: `tests/engine/test_outcome_attribution.py`

**Interfaces:**
- `DecisionSnapshot` stores decision_id、rule_version、params_hash、data_as_of、planned action。
- `settle_decisions(as_of, price_history, executions) -> attribution_summary`。

- [ ] **Step 1: 写 1/5/20 日结算测试**

使用确定性价格序列；验证 executed 与 shadow 未执行反事实分开；扣交易成本和申赎延迟。

- [ ] **Step 2: 写样本门测试**

样本 <10 只输出 count/raw outcomes，不输出 win_rate；>=10 才允许统计且必须带置信区间或明确非统计结论。

- [ ] **Step 3: 实现快照和结算**

每个 approved action 保存 DecisionSnapshot。定时 run 对到期 horizon 结算，幂等写入。

- [ ] **Step 4: CLI 输出**

增加 `--decision-attribution` 和 `--decision-attribution-settle --now ...`，仅输出结构化 JSON。

- [ ] **Step 5: GREEN + Commit**

`git commit -am "feat: attribute approved decisions with execution costs"`

---

### Task 12: 总验收与文档收口

**Files:**
- Modify: `EXECUTION_PLAN.md`
- Modify: `PLAN.md`
- Modify: `ARCHITECTURE.md`
- Modify: `stocks/DATA_MODEL.md`
- Modify: `AGENT_GUIDE.md`
- Create: `docs/T1_DECISION_TRUST_ACCEPTANCE_20260715.md`

- [ ] **Step 1: 全局静态与测试闸**

Run:
- `uv run ruff check .`
- `uv run python -m pytest -q`
- `uv run python -m compileall -q stocks tests`
- `git diff --check`

Expected: 全部 exit 0。

- [ ] **Step 2: 真实 CN 验收**

强制 `cn_pre_close`：验证 A 股 freshness、半导体 anomaly、真实 cash schedule、portfolio decision、risk transition、五段报告。

- [ ] **Step 3: 真实 US 验收**

强制 `us_pre_open` 和 `us_after_close`：previous_close 与盘后语义正确，QDII T+2 不进入即时现金。

- [ ] **Step 4: 反例注入验收**

用测试 Fixture 注入 stale brief、黄金超配 add、权益低配 reduce、单 critical cluster、无变化 watch，逐一证明系统 suppress/review/silent。

- [ ] **Step 5: 独立双审查**

一名 reviewer 只审代码契约和测试；另一名 reviewer 只从真实交易者视角审最新 JSON/MD。任何 P0 异议未关闭不得宣布完成。

- [ ] **Step 6: 更新 T1 状态**

仅在 Slice A–C 全部真实验收后关闭 T1-P0。Slice D 进入 20 交易日试运行，不得提前标完成。

- [ ] **Step 7: 最终 Commit**

`git commit -am "docs: close decision trust implementation gates"`

---

## 实施管理

- 推荐每个 Task 独立 worktree/branch，Task N 通过 reviewer 后才能合并并开始依赖它的 Task。
- 可并行：Task 3 与 Task 4 在 Task 1 完成后并行；Task 6 可与 Task 7 并行。
- 必须串行：1→2；4→5；7→8→9；5+9→10→11；最后 12。
- 每个 Task 都必须更新 `EXECUTION_PLAN.md` 的证据，不接受只在 commit message 声称通过。
