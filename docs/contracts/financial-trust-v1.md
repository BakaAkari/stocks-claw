# 个人约束、金融事实核对与 LLM 责任链

契约生命周期只见 [索引](README.md)，验证和部署状态只见 [STATUS](../../STATUS.md)。

## 个人约束在分析之前

`profile.mandate` 保存 goals、horizon、loss_capacity（amount/currency）、cash_needs
（account_id/currency/amount/needed_at）、operation_frequency、style_preferences。
`profile_update` 合并已提交字段，显式 null 清除；`constraints_replace` 整体替换组合约束，
明确提交空对象才表示无配置限制。两个写入口都需要 confirmed。

`personal_mandate` 冻结值、来源、确认状态、未知项和内容哈希。配置示例、推断画像和
交易历史不能自动变成用户已确认的风格。偏好未知保留为提示；自由文本目标和损失能力
进入模型上下文，不宣称已被机械证明适当。偏好不等于值得强化的交易习惯。

## 真实账务事实

- `AccountStatement` schema 1：id、account_id、as_of、recorded_at、source，
  cash_basis=settled、position_basis=trade_date，scope=complete_account 或 listed_only。
  cash 行包含 currency/balance/available_balance；positions 行包含
  instrument_id/quantity/unit/available_quantity。可用值可未知，不从账面值推算。
- `ExecutionRecord` schema 3：明确 manual 或 advice_linked，保留账户、来源、单位、
  买卖方向、数量、价格、币种、费用、成交时间及可选结算时间。自主交易无需伪造建议。
  settled_at 表示本金和所列现金费用均到账；缺费用或结算时点不补零、不猜 T+1。
- `CashMovement` schema 1：独立记录入出金、分红、利息、转账、换汇、费用等现金腿。
  每腿有 account_id/currency/amount/effective_at/recorded_at/source。交易本金来自成交；
  不在币种或账户之间隐式转换。

新事实分别保存在 `.local/account_statements/`、`.local/executions/` 和
`.local/cash_movements/`。写入经 CLI/MCP 确认门；相同 ID 同内容幂等，冲突拒绝。
坏文件显式报告，不能静默丢弃后仍声称核对成功。

每次 confirmed CLI/MCP 写入同时在 `.local/confirmation_events/` 生成不可变事件，包含
`event_id`、目标、`content_hash`、`source_kind=user_confirmed`、渠道、确认时间、来源路径和事件封套完整性哈希。
确认事件与当前业务内容哈希必须相等才有效；旧文件、手工修改文件或缺失 sidecar 只能是
`legacy_unverified`，不能因为文件存在就升级为用户确认事实。重复提交复用同一事件，不以
新的时间戳制造第二个确认身份。资产账本同样必须有匹配的确认事件，文件内的 `confirmed=true`
不能单独进入 `financial_integrity` 的可信匹配。

`financial_reconcile(opening_id, closing_id)` 用十进制算术核对
`期初 + 区间逐笔变动 = 预计期末`，与独立期末记录比较。按账户、币种、标的和单位
分开，输出 matched/mismatch/incomplete、差额、记录引用、哈希和缺口。
`financial_integrity` 另核对当前资产记忆与最近账单；重复持仓 ID、未知账户、后续事件
或资产差异不能获得可信匹配。以上读取不自动修改持仓、资金或成交。

## Hermes 唯一操作入口

Hermes 通过 MCP 负责编排本系统；CLI 是同一确认边界的本地兼容入口，不是另一套业务流程。
Hermes 应先调用 `agent_status` 读取当前核对状态、个人约束来源和下一步，再按顺序使用
`asset_intake_draft` → `asset_intake_confirm` 录入资产，使用 `statement_save`、
`cash_movement_save` 和 `execution_save` 记录账户事实，使用 `financial_integrity` 或
`financial_reconcile` 核对，使用 `get_analysis_context` 生成建议上下文，最后使用
`decision_review` 复盘。`asset_intake_draft` 只生成草稿；确认接口要求原草稿、未过期 token
和 `confirmed=true`，任何记忆变化或歧义都会拒绝且不写入。

所有写入接口失败时必须停止当前链路并返回原始错误；不得用推断值重试、把建议当决定、
把决定当成交，或在核对不完整时继续生成可执行组合动作。重复请求应复用原 ID/token，
冲突请求必须保留原记录并报告冲突。`agent_status`、所有列表、核对、上下文和复盘接口
均为只读；分析和复盘永远不能改写用户金融事实。

matched 只表示已提供记录在声明区间内一致，不证明券商真实性、记录完整性、当前
可用资金或利润。历史核对一致与当前可执行资格分开；LLM 检查冻结的账单时效政策。

## LLM 的责任与边界

正式上下文保留 holding 数量和单位，连同个人约束、核对结果、行情、执行规则进入
完整内容哈希的统一快照。保留传入模型的提示词、请求模型标签、原始返回、解析结果、
失败尝试和校验回执。请求模型标签不是服务端模型版本的独立证明；当前 complete
接口未暴露完整 HTTP 消息和响应元数据时，记录范围为 complete_argument_only。

主窗口使用 LLM 原始提案，经引用、标的/账户、可用数量、时效、单位和已支持约束检查
后投影为操作卡片；规则作为证据保留，不在模型失败时冒充已验证的 LLM 动作。
输入哈希、建议/action ID、回执哈希一直保留到卡片和冻结来源建议。原始 sell/buy
语义不被展示层丢弃。被阻断提案保留原因，不能显示成模型认为无需操作。

首版只放行有明确数量的既有 share 持仓卖出/减仓，且必须满足所有可验证条件。
买入缺费用预算、复杂配置限额缺组合投影、条件触发无法验证等保持待复核。
账单可用事实默认最多 86400 秒，策略值冻结进快照和回执。
这不是收益、适当性或完整现金规划证明；未知组合资金值不能显示成零。

已验证结构可继续走来源建议 → 用户决定 → 每笔成交 → 只读复盘。
`run_llm_report.py` 的二次文字改写与最终送达尚无完整证据，结构哈希不代表消息已发送。

## 验收

先验证账户事实和个人约束，再验证模型责任链，最后才考虑统计。必须从真实入口构建
上下文，不能手工补字段绕过生产断点；使用项目的执行规则。模拟账户、行情、模型
响应要明确标记，不能当作真实账户或在线模型验收。

```bash
.venv/bin/python scripts/smoke_financial_reconciliation.py --output-dir /tmp/finance-example
.venv/bin/python scripts/smoke_advisory_lineage.py --output-dir /tmp/advisory-example
```

输出必须是新的项目外数据目录；执行代码仍是当前项目，没有隔离代码副本。
公开 CLI 读取为 `--personal-mandate`、`--financial-integrity`、
`--financial-reconcile OPENING_ID CLOSING_ID`、`--statement-list`、`--cash-movement-list`。
写入为 `--profile-update JSON`、`--constraints-replace JSON`、`--statement-save JSON`、
`--cash-movement-save JSON`、`--execution-save JSON`，均需用户确认后使用 `--confirmed`。
