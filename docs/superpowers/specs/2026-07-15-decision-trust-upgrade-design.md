# stocks-claw 决策可信度提升设计

> 日期：2026-07-15
> 状态：T1 实施基线

## 1. 目标与边界

不再增加扫描池、情报源或指标。把系统从“列出局部建议”改为“证据合格→单资产判断→组合裁决→风险放行→可执行报告→结果归因”。系统仍不自动下单、不承诺收益；未通过任一闸门的建议只能进入研究区。

## 2. 强制不变量

- Action Card 生成后不可被 Capital Allocation 修改。
- 数据异常优先阻断交易动作；风险硬约束优先于普通信号。
- 今日现金只包含已到账现金/T0；未卖出的证券市值不是可用现金。
- 未裁决冲突不得产生 approved action。
- 今日动作与研究候选完全分离。
- 所有阈值必须客观可测、可测试、可回放。

## 3. 目标决策链

`PositionEvidence → RawActionCard → PortfolioDecision → RiskGate → ExecutionPlan → WindowReport → ExecutionRecord → OutcomeAttribution`

每层只消费上一层输出，不反向修改输入。

## 4. 结构化设计

### 4.1 PositionEvidence

每个 position valuation 增加 evidence：market、price_as_of、price_freshness、indicator_as_of、indicator_freshness、valuation_as_of、valuation_freshness、data_anomalies、action_eligible、blocked_reasons。

freshness 固定为：current、previous_close、t1_confirmed、manual_current、stale、missing。全局 quotes.freshness 只用于运行健康，不得参与单持仓 ratio。

### 4.2 DataAnomalyGate

在指标计算后、技术规则前检查：

- single_bar_jump：相邻交易日绝对变化 >35%。
- price_ma20_dislocation：现价与 MA20 偏离 >30%，且近 20 根存在 >25% 跳变。
- prev_close_mismatch：prev_close 与上一根收盘差异 >5%。
- mixed_adjustment_regime：60 根序列存在 >35% 跳变且比例接近 1:2/2:1 等拆并区间。
- source_regime_change：异常点附近数据源变化。

block 级异常令 action_eligible=false，所有技术动作暂停。系统不自动解除，必须数据修复或人工确认。

### 4.3 RawActionCard

行动卡增加 raw_signal、raw_ratio、raw_action、evidence_status、portfolio_status、execution_status。Capital Allocation 不得原地修改 action_cards。

### 4.4 CashSchedule

替换 deployable_value_cny 单值，输出：immediate_cash_cny、same_day_sale_proceeds_cny、t1_available_cny、t2_or_nav_pending_cny、strategic_exit_value_cny、locked_value_cny、safety_buffer_cny、immediate_deployable_after_buffer_cny。

未获批卖出的 ETF、股票、基金、黄金只能进入 strategic_exit_value。

### 4.5 PortfolioAdjudicator

新增 stocks/engine/portfolio_adjudicator.py。输入为 RawActionCard、PositionEvidence、约束、CashSchedule、RiskState。输出：status、approved_actions、suppressed_actions、replacement_chains、unresolved_conflicts、post_trade_projection、cash_schedule。

优先级：数据异常→不可交易→硬止损纪律→风险状态→组合约束→容量/最小额/结算→研究候选。

权益低配但权益减仓时，必须形成卖出腿+到账时间+替代买入腿+触发条件+执行后比例，或 status=review_required。只列 conflicts 不是裁决。

### 4.6 RiskState

新增 .local/state/risk_state.json，原子写入：level、first_triggered_at、last_confirmed_at、consecutive_confirmations、active_triggers、clear_conditions、expires_at、previous_level、transition。

单个新闻聚类不能直接 normal→hedge，除非第二独立证据或连续两轮确认。critical 聚类必须同时满足 published_at 和 snapshot_at 时效。降级需要两轮确认或明确解除条件。状态有 TTL。

### 4.7 IntelligenceHealth

消费情报前比较 latest_brief、latest global watch、digest 时间。信号增加 generation_method（llm/rule_fallback/category_padding）与 match_method（exact/proxy/exposure_tag/category）。category padding 不计方向覆盖；过期 brief 不参与风险升级。

### 4.8 WindowReport

固定五段：本窗口变化、今日获批动作（最多3项）、禁止/暂停动作、执行后组合影响、下一检查点。研究候选标记 research_only=true。

Watch Window 只输出 Delta；无 approved action、风险变化、trigger 变化或异常变化时 archive_only。

### 4.9 Execution 与 Attribution

扩展 ExecutionRecord：run_id、decision_id、planned_ratio、executed_ratio、executed_price、status（executed/partial/rejected/deferred）、rejection_reason。

归因保存规则版本、参数快照、1/5/20 交易日结果、成本、申赎延迟。样本不足只报告样本，不宣称策略有效。

## 5. 切片顺序

- Slice A：逐持仓 freshness → anomaly gate → intelligence health/provenance。
- Slice B：Action Card 不可变 → CashSchedule → PortfolioAdjudicator → 参数语义修正。
- Slice C：RiskState → priority/notification → Delta Report。
- Slice D：Decision/Execution 关联 → 版本化归因 → Shadow Trial。

## 6. 停止条件

默认测试非全绿；未裁决冲突仍有 approved_actions；异常数据持仓获批；immediate cash 包含未卖证券；风险转换无时间/证据/解除条件；无变化 Watch 仍 push_now；报告出现 Artifact 不存在的事实。任一命中即停止后续切片。

## 7. 验收

- A 股 current 不受美股 previous_close 影响。
- 半导体 ETF 50% 跳变阻断 MA20 动作。
- T-1 基金、手工黄金、实时 ETF 有独立 freshness。
- 过期 brief 不参与风险升级。
- Action Card 在组合模块前后字节级等价。
- 权益低配+权益减仓必须换仓链或 review_required。
- 黄金超配不批准增加黄金风险。
- 今日现金不含未卖持仓。
- 单个短寿命 critical 不导致 hedge 后立即 normal。
- Watch 无变化静默；今日动作最多 3 项。
- 记录至少 10 个 Decision 状态并完成至少 20 个交易日 Shadow Trial。

## 8. 可信度目标

Slice A 完成后事实层目标约 95%。Slice B/C 完成后达到可供人工确认执行的 70–80%。Slice D 积累足够真实样本后，才评价长期 80% 以上可信度；测试不能替代真实反馈。
