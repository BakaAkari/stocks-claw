# 单账户资料预检与就绪检查

生命周期见 [契约索引](README.md)。这是只读入口，不是批量导入器、用户确认或建议许可。

## 用户需要准备什么

一次先核对一个 A 股或美股账户。账户使用稳定别名，无需登录密码、验证码或完整账号。

1. 两个不同时点的券商完整账单，注明时区、来源和各自唯一编号；不是从成交倒推出的账单。
2. 两个时点的全部币种现金余额、可用现金，以及全部持仓代码、数量、单位、可用数量。
3. 两个时点之间的逐笔买卖：账户、标的、方向、实际数量、价格、币种、实际费用、成交时间、结算状态与已发生的结算时间。此前成交但在区间内结算的记录也要包含。
4. 同期非交易现金变动，例如入出金、分红、利息、费用，保留金额、币种、类型、来源与发生时间。

未知字段保留未知，不能默认为零。确认没有持仓、成交或现金变动时对应列表可为 `[]`；
不能为通过校验而遗漏账户的一部分。当前预检仅覆盖普通 A 股/美股 share 单位的手工成交；
复杂证券或公司行动先保留缺口，不强行改成普通买卖。

静态模板：[A 股](../examples/account-intake-a.json)、[美股](../examples/account-intake-us.json)。
模板中的 `null` 必须按实际资料补充；现金币种列表需要覆盖账户的所有币种。
成交与现金变动字段沿用 [金融事实契约](financial-trust-v1.md)，成交采用
`schema_version: 3`、`record_origin: manual`，不伪造建议来源。

## 只读操作

```bash
.venv/bin/python -m stocks.adapters.cli --financial-intake-template a --output json
.venv/bin/python -m stocks.adapters.cli --financial-intake-preview-file /absolute/private/account.json --output json
.venv/bin/python -m stocks.adapters.cli --financial-readiness --output json
```

模板命令的 `data` 是待填的 bundle，文件预检读取该对象本身，不读取外层 `success/data` 封套。
资料文件应放在仓库外的私人目录，不提交到版本控制。输出也可能含私人资料。
MCP 对应 `financial_intake_template(market)`、`financial_intake_preview(bundle, as_of?)`、
`financial_readiness(as_of?)`；`agent_status` 同时返回就绪检查结果。

`needs_correction` 返回具体路径错误或账实差异；不会自动修账。
`ready_for_user_review` 只表示这份未确认草稿能够核对，不表示资料新鲜或已入库。
缺少 `recorded_at` 时只补充本次采集时间，绝不把它当作券商观察时间。
全部预检都返回 `requires_confirmation: true` 和 `advisory_ready: false`。

核对后先展示拟写入的逐项事实，经用户明确确认，再调用现有独立的资产、画像、约束、
账单、成交与现金变动确认接口；预检本身不会调用任何写接口，也不承诺多记录原子导入。

## 就绪语义

`current_facts_available` 要求确认来源、完整核对、画像中的损失承受能力和现金需求、
确认约束，以及不过期的账单和已知可用现金/数量。默认账单最大年龄为 86400 秒，
由 `financial_statement_max_age_seconds` 控制；历史核对通过不等于今天可用。
未来账单被阻断。结果仍不是动作批准、L2 验收或部署许可。

分析快照另有 90 分钟年龄限制，缺失、无时区或超前超过 60 秒会在调用模型前阻断。
60 秒只容忍构造时间和微小钟差。宏观字段只采用各自来源的 `as_of`，缺失保持未知。

隔离模拟验证：

```bash
.venv/bin/python scripts/smoke_account_readiness.py --output-dir /tmp/stocks-account-readiness-new
```

目标必须为仓库外的新目录。模拟覆盖 A 股/美股预检、跨账户拒绝、过期阻断，
并比较只读调用前后的金融文件哈希；不代表真实券商、在线模型或投资有效性验收。
