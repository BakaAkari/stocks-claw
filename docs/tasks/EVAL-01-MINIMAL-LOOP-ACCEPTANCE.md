# EVAL-01 最小观察闭环验收记录 — 2026-10-15

本记录是一次有界修复的证据，不代表完整 E4、策略有效性或恢复上线验收。
当前动态状态只见 [STATUS](../../STATUS.md)，字段与业务口径见
[最小评价契约](../contracts/signal-evaluation-v1.md)。

## 基线与工作位置

本地 master、origin/master 基线为 `b288a96`。直接在当前项目
`/Users/baka_akari/code/stocks-claw` 修改；无隔离代码副本或 worktree。
修复未提交/推送。NAS 未部署，现有 cron/账本/用户金融记忆未写入。
此前 E1–E3 验收仅支持各自模块能力，不再解释为端到端链已闭环。

## 真实行情的纵向验收

```bash
.venv/bin/python scripts/smoke_evaluation_pipeline.py \
  --output-dir /tmp/stocks-evaluation-acceptance-20261015
```

通过真实公共 Binance 日线重放现有 `compute_rotation` 和 `compute_action_signals`，
得到 `crypto:BTCUSDT` 的 `accumulate_candidate`。历史 entry 经过正式观测适配器，
再传给生产使用的 `_track_engine_action_signals`。随后使用正式同步器、账本、
worker、provider 和报告读取函数；没有手写一个“缺什么补什么”的 signal fixture。

**性质是历史回放**：`provenance.origin=historical_replay`，
`production_signal_emitted_at_historical_time=false`。它不声称在历史时刻发出过信号，
也不是完整 scheduled-run / LLM / 推送验收。历史输入、规则代码与配置哈希均在产物中。

| 项目 | 实测值 |
|---|---|
| 信号/entry 时刻 | 2026-10-14 23:59:59.999 UTC |
| 明确窗口 | elapsed 1 小时 |
| target / exit 时刻 | 2026-10-15 00:59:59.999 UTC |
| entry / exit | 81682.01 / 81656.01 USDT |
| price_return | -0.0003183075440968963 |
| 首次同步/评价 | scheduled=1 / evaluated=1 |
| 重复同步/评价 | scheduled=0、existing_or_duplicate=1 / not_claimed=1 |
| 唯一性 | result_count=1、idempotent=true、两次报告内容完全一致 |

该涨跌只用于检验算式，不是策略输赢。报告 `outcome=null`，不因候选的动作语义翻转收益。

最终运行标识：

```text
signal_id: signal-v1:8cae9a907343a66d12e01c6e0a7fc208cacc92db739d30c2046c7a277a2581f9
policy_id: raw-price-observation-v1:crypto:smoke-raw-price-1h-v1
input_hash: d91466bf2edb6feea455c55d6895ea8b11b5bf12b39ab5f26a80f4d233502065
observation_hash: 91a5c05233d1d5d7aa23b985e7d044d632ff0b29b105ea77cd6bc353f5036da3
result_id: 99af6617a3b0dd8ff72d7ed503a0c1072f9b5eb0704c0fd5d806a612aa7940cd
```

本机产物目录 `/private/tmp/stocks-evaluation-acceptance-20261015/` 包含
`history-input.json`、候选搜索记录、规则输出、entry 原始观测、版本化 signal JSONL、
窗口配置、`evaluation.sqlite3`、`report.json` 和 `smoke-result.json`。
临时产物可能随系统清理；以上命令可重新执行至新的空目录。
每次重新取数有新 retrieval/replay 时间，内容哈希可以不同；同一冻结数据库重复运行必须一致。

## 反例与运行检查

聚焦测试覆盖：

- 真实生产入口保留等待/减风险等动作；缺价或身份冲突不生成可评价假数据。
- 同 ID 内容变更、同 policy ID 窗口变更被拒绝，并且没有半写入的新 signal/policy。
- worker 不能创建任务；旧任务不混入新结果；pending、重试、过期各有独立原因。
- provider 超过租约后不能提交；10 个并发 worker 只产生一个结果；结果写入失败时
  观测/任务状态一起回滚。NaN 等非法响应保留失败原因而不丢失 attempt。
- target/session 不一致、entry 标的错配、错误币种、原始响应哈希/解析字段被改均被拒绝。
- 外部信号文件变化不改报告；运行时评价器版本变化不重算旧任务；报告在并发提交下
  仍读取同一快照；独立 CLI 导出的只读报告不修改数据库。

最终验证：全量 `pytest -q -o 'addopts='` 为 **1560 passed / 7 skipped**；
变更 Python 文件 Ruff、`compileall stocks tests scripts`、`git diff --check` 通过。
全仓 Ruff 仍为 48 条既有问题，按路径/诊断与修改前结果比较无新增；未为本任务
修复无关文件，也不宣称全仓 lint 已通过。

## 未覆盖范围

完整策略成效、胜率及置信区间、最大回撤、费用/分红/拆股收益、股票生产信号纵向
验收、长期覆盖率和持续运行恢复均不在本次验收内。新生产事件缺少可信 strategy
version 时仍保留 unknown；本次不能证明完整历史分析可重放。旧账本与旧任务不迁移。
