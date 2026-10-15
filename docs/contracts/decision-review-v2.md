# 建议、用户决定、成交与复盘契约 v2

本契约定义事实和责任。运行状态及验证结果见 `../../STATUS.md`；生命周期见
`README.md`。它复用原始报价观测管线，不增加策略收益指标。

## 来源与身份

`scheduled_analysis.py` 在生成 `portfolio_decision.user_view` 后，为每条 approved /
suppressed action 保存一份 RecommendationSnapshot v2：`decision_id`、
`source_run_id`、`generated_at`、`recommendation_status`、完整 `action`、
`position`、`instrument_id`、`entry_snapshot`、`report_view`、相关 `context`、
`rule_version` 和 `source_hash`。它保存报告结构，未声称是最终推送文本或送达回执。

标的必须来自唯一匹配的 position valuation，不从持仓 ID 猜代码；缺信息保留原因。
批准建议不代表用户接受或成交。每动作只存一份，不按窗口复制“已执行”快照。
文件是 `.local/decisions/recommendation-<decision_id摘要>.json`；自定义
`local_data_dir` 同时控制生产写入与复盘读取。完全相同内容重试幂等，同 ID 变化拒绝，
不覆盖旧版本。强制重跑也不能改变已冻结的同一建议身份。

## 用户确认的事实

写入都经过 CLI `--confirmed` 或 MCP `confirmed: true`。只能在用户明确确认实际
内容后调用；本契约中的示例不代表用户已经做过任何交易。

- AdviceRecord：稳定 `id`、`created_at`、`source_run_id`、完整 `original_text` 和
  `source_snapshot`；actions 保留 `decision_id`。来源没有持有期限就用 `unspecified`。
  源建议保存接口只接受 `source_decision_id`，避免调用者替换来源字段。
- UserDecisionRecord：`id`、`advice_id`、`decision_id`、`status`（accepted / partial /
  rejected / deferred）、`decided_at`、`recorded_at`、`note`。每次决定单独追加。
  最新拒绝不会删除先前采纳或已经记录的成交。
- ExecutionRecord v2：`id`、`advice_id`、`decision_id`、`source_run_id`、`target`、
  `status=executed`、`side=buy|sell`、`quantity`、`price`、`currency`、`executed_at`、
  `recorded_at`；可选 `user_decision_id`、`fee_amount`、`fee_currency`、`account_id`、
  `note`。quantity / price 必须有限且为正。时间有时区，不得用记录时间补造成交时间。
  费用未知保存 null，不能当零。拒绝/延期写 UserDecisionRecord，不冒充成交。

决定与成交 API 要求显式事件 ID；省略 recorded_at 时首次由系统生成，重试复用。
相同 ID/内容幂等，修改内容拒绝。不同 ID 视为独立事实，调用方需复用原 ID 防重复。
没有记录当时决定也可以确认真实成交，但复盘保留决定关联缺口。
实际买卖方向可以与建议相反，不能把这种成交标为完整执行原建议。

建议、决定、成交不滚动裁剪。旧 advice feedback 保留 latest 兼容投影并追加
feedback_history；它不替代有时间和动作身份的 UserDecisionRecord。
旧手工建议/成交可以通过原入口读写，但不自动升级成可信来源链。
成交登记不自动修改账户余额、持仓、成本批次或投资者画像；这些仍通过各自确认入口维护。

## 只读复盘

`decision_review`（旧 `decision_attribution_settle` 为同一只读入口）返回
`by_decision`，每项包含来源快照、建议、全部决定及最新决定、全部成交、逐笔现金流、
按币种汇总、后续冻结价格及 `gaps`。`as_of` 必须带时区，同时遵守实际事件时间和
记录/观测取得时间，不泄露之后才知道的事实。相同输入和 as_of 产生相同 content_hash。

没有成交记录为 `unrecorded`，不能断言用户未交易。只有同建议方向且建议数量明确
才判断 partial / full / exceeds_recommendation；其他已录入成交为 recorded_fills。

同币种费用明确时：buy 现金流 = `-quantity*price-fee`，sell = `quantity*price-fee`。
费用未知或异币种时净现金流 unavailable，可单列 gross_cash_flow；各币种不混加。
卖出现金流不是利润。无成本批次和完整持仓路径时 realized_pnl unavailable；没有
可比资金基线时 portfolio_gain unavailable。没有胜率、净收益或回撤的完成声明。

价格通过精确 `decision_id + source_snapshot_hash + symbol` 关联同一 EVAL 冻结结果。
建议观察桥只调用原 `schedule_signal`，worker 仍消费唯一冻结任务。复盘不访问实时
行情、不重建任务、不写账本。旧 executed/settled 快照只作 legacy_unverified 历史。

## 使用入口

先读取生产来源 `--decision-attribution`，再由用户确认相应事实。示例 ID 与数量仅为
字段说明；实际操作必须使用用户确认的值：

```bash
.venv/bin/python -m stocks.adapters.cli --advice-save '{"source_decision_id":"<decision_id>"}' --confirmed
.venv/bin/python -m stocks.adapters.cli --decision-save '{"id":"<choice-id>","advice_id":"advice:<decision_id>","decision_id":"<decision_id>","status":"partial","decided_at":"2026-10-15T10:00:00+08:00","note":"先执行一部分"}' --confirmed
.venv/bin/python -m stocks.adapters.cli --execution-save '{"schema_version":2,"id":"<fill-id>","advice_id":"advice:<decision_id>","decision_id":"<decision_id>","user_decision_id":"<choice-id>","status":"executed","side":"sell","quantity":100,"price":8.5,"currency":"CNY","fee_amount":1,"fee_currency":"CNY","executed_at":"2026-10-15T10:01:00+08:00"}' --confirmed
.venv/bin/python -m stocks.adapters.cli --decision-review '<decision_id>'
```

对应 MCP：`advice_save`、`decision_save`、`execution_save`、`decision_list`、
`decision_review`。新决定和全部成交也进入 AnalysisContext 的 recent_advice 执行对照。

观察窗口必须显式选定。示例 `windows.json` 为
`{"a":[{"id":"raw-close-3s-v1","basis":"sessions","horizon":3,"retry_seconds":86400}]}`。
这只定义三个后续交易日的原始收盘报价观察，不改变原建议期限。

```bash
.venv/bin/python scripts/collect_target_observations.py \
  --database .local/evaluation.sqlite3 --recommendations .local/decisions \
  --decision-id '<decision_id>' --windows /path/to/windows.json
.venv/bin/python scripts/collect_target_observations.py --database .local/evaluation.sqlite3
.venv/bin/python -m stocks.adapters.cli --decision-review '<decision_id>' \
  --evaluation-database .local/evaluation.sqlite3
```

## 产品价值验收

工程闭环通过意味着可以可靠回答“当时建议、我的选择、我的成交、随后行情和信息缺口”。
它不意味着建议优于用户原计划或简单规则。后续价值验收必须事前冻结对照方案、资金与
风险约束、观察期和成本假设，分别检验信息质量、纪律偏离、研究耗时及可比投资结果。
拒绝建议可能是正确决定，采纳率不能替代投资效果；无法区分偶然性时明确证据不足。
