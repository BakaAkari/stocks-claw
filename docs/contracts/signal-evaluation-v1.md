# 最小信号评价契约 v1

本契约限定第一条纵向闭环的含义。生命周期见 [契约索引](README.md)，
当前工作状态见 [STATUS](../../STATUS.md)。原 EVAL-01 的方向胜率、净收益、
回撤和策略专属评价属于后续范围，不能从本契约的结果中推断。

## 输出的唯一指标

`price_return = exit.price / entry.price - 1`，数值单位为比率。
它描述同一标的、同一报价币种的两次原始报价变化；`USDT` 不等同于 `USD`。
入场快照是信号生成时的参考价格，不表示成交。原始报价也不证明拆股调整一致，
更不包含分红、费用、滑点或汇率转换。

所有结果均为 `scoring_status=observation_only`、`outcome=null`，报告设置
`feedback_enabled=false`。`wait_for_pullback`、`reduce_risk`、
`left_bottom_candidate` 等原始语义保留，不映射成买卖方向，也不据此改变涨跌符号。
情报来源已有的 direction 可保留，但同样不取得方向评价资格。

## 一条调用链与写入责任

```text
scheduled_analysis 的真实信号记录入口
  → SignalTracker.record（版本化 JSONL 事件）
  → evaluation_task_sync.schedule_signal（唯一任务定义入口）
  → EvaluationLedger.freeze_task（冻结 signal + policy + target）
  → observation_pipeline.collect_observation（只消费冻结任务）
  → target_observation.observe（目标行情与来源证据）
  → EvaluationLedger.record_attempt（观测、结果、终态原子提交）
  → evaluation_metrics.build_report（同一只读快照中的已冻结结果）
```

worker 不能另建任务、重读信号文件或重新计算窗口。报告不重新评价，也不按
policy 字符串前缀筛选结果。旧 generic task API 保留兼容测试；没有冻结定义的
`directional:*` / `observation-v1:*` 任务不会被新 worker 消费，报告单独计数排除。
旧 JSONL 不自动升级、补字段、迁移或重算；旧结算入口拒绝修改版本化新事件。

## 冻结输入

- **SignalEvent**：`schema_version=1`，canonical `market:code`、有时区的
  `generated_at`、`signal_id`、`signal_type`、`action_semantics`、`strategy_id`、
  `strategy_version`、原始理由、来源及 `entry_snapshot`。没有可信策略版本时
  保留 `unknown`；这允许价格观察，不支持策略版本成效比较。现有生产流程没有
  提供的 decision/config/model lineage 不补造，完整分析重放不在本闭环验收范围。
- **事件身份**：由来源、UTC 生成时刻、标的、原始动作和 production/replay 来源
  共同确定；相同 ID 相同内容重复写入是 no-op，不同内容拒绝。各生成时刻的事件
  均保留，不能把它们当成独立统计样本。跨进程查重与追加由文件锁保护。
- **EntrySnapshot**：标的必须匹配信号，价格为有限正数，来源和时区时间已知，
  `as_of <= generated_at`，`stale=false`，币种明确，
  `price_basis=raw_quoted_price`。适配器只填写自己能够证明的元数据；缺少币种或
  价格基准时同步器记录跳过原因。本版本不为股票生产入口人工补这些标签。
- **EvaluationPolicy**：显式定义 `id`、`basis`、正整数 `horizon`、非负整数
  `retry_seconds`。`basis=elapsed` 的单位是自然小时；`basis=sessions` 表示
  生成时刻之后第 N 个交易会话收盘（严格晚于生成时刻）。没有默认“24h”或“1w”。
  policy 同时冻结市场、指标、价格基准和评价器版本。
- **Target**：冻结 UTC `target_at`、basis、session、calendar、交易所时区；
  会话窗口同时记录日历库版本。重复同步沿用已冻结目标。校验会核对实际会话
  收盘时间；日历版本变化导致不一致时拒绝观测，不悄悄移动目标。

数据库以 `(signal_id, policy_id)` 冻结唯一任务定义，任务执行键另含 `target_at`。
signal 和 policy 还分别全局冻结；相同 ID 的内容冲突整体回滚，不覆盖旧值。
窗口含义改变必须使用新 policy ID。`input_hash` 绑定完整 signal、policy、target。
升级评价器不会自动重算旧任务；运行时版本与冻结版本不同会拒绝处理。

## 观测、重试和结果

成功观测保存请求 URL、解析前 JSON 响应、规范 JSON 的 SHA-256、标的、目标定义、
价格、币种、价格类型、来源时间及其精度、观测时间、取数时间和偏离目标的秒数。
验收时重新解析保存的响应，验证其与提取字段、请求参数和目标一致。

- 加密资产选取目标时刻之后或恰好等于目标的首个 1 分钟收盘。真实 bar 开闭时间
  必须匹配；最多约一分钟的偏移显式记录，不称为精确目标 tick。
- 股票仅支持会话日线；自然小时窗口缺分钟源时明确 unavailable。当日会话的日线
  完结性未获证明，至少到交易所当地次日才接受。日线 `source_as_of=null`，
  `observed_at` 明确来自交易所日历；不能伪称 provider 提供了收盘时间戳。
  Yahoo 的调整口径记录为 `provider_adjustment_unspecified`。
- 领取任务时的租约使用实际推进的时钟；提交再检查租约和 token。超时 worker
  不能写入，后续 worker 可重新领取。
- 取数失败在重试截止前保存 attempt 并恢复 scheduled；报告显示 retrying 及最近
  原因。截止后仍缺有效观测才冻结 expired。首次晚到的采集若确实取得目标历史
  bar 可以回填该任务；已经终态的任务不能重开。
- 有效观测的币种/价格基准与 entry 不一致，冻结 unavailable；成功则冻结
  evaluated 并将任务置为 completed。观测、结果及任务终态同一事务提交。
  尚未到期的任务显示 pending，不能伪装成 target_mismatch。

`EvaluationResult` 包含完整冻结输入、entry、exit、输入及观测哈希、评价器版本、
status/reason 和上述唯一指标。`result_id` 哈希绑定这些内容；数据库对任务键与
result ID 均施加唯一约束。只读报告从 SQLite 的同一快照读取结果与任务状态，
外部信号文件和配置随后变化不改变既有结果。

## 手动运行

窗口文件示例（调用者明确选择 1 自然小时；不是项目默认配置）：

```json
{"crypto":[{"id":"raw-price-1h-v1","basis":"elapsed","horizon":1,"retry_seconds":7200}]}
```

```bash
# 首次同步版本化事件并采集已到期任务；路径由操作者指定。
.venv/bin/python scripts/collect_target_observations.py \
  --signals /path/to/signals.jsonl --windows /path/to/windows.json \
  --database /path/to/evaluation.sqlite3
# 后续 worker 只需要数据库。
.venv/bin/python scripts/collect_target_observations.py \
  --database /path/to/evaluation.sqlite3
.venv/bin/python scripts/build_evaluation_report.py \
  --database /path/to/evaluation.sqlite3 --output /path/to/report.json
# 真实公共行情 + 现有规则代码 + 真实记录入口的历史回放验收。
# output-dir 必须是新的或空的数据目录；不会复制项目代码。
.venv/bin/python scripts/smoke_evaluation_pipeline.py \
  --output-dir /tmp/stocks-evaluation-smoke-new-run
```

烟测调用现有 `compute_rotation` / `compute_action_signals`，按时间顺序寻找已到期
候选，不按事后收益筛选；来源标为 `historical_replay`，记录回放创建时间、历史
决策时刻、输入与代码/config 哈希。它证明生产代码入口可贯通，不证明历史时刻
真的发出过该信号，也不证明持续运行覆盖率。长期样本、股票纵向闭环、统计分母、
费用/企业行动口径、最大回撤及系统恢复均需后续独立验收。
