# Agent 使用指南

> 本文只覆盖**运行操作**和**金融数据安全**规则，供 AI Agent 和开发者操作当前
> 系统时参考。开发流程、任务范围、文档权威顺序、session 纪律和 commit/push
> 规则的唯一来源是 `AGENTS.md`——本文不重复、不覆盖、也不与之竞争那些规则。
> 如果这两份文档看起来对"接下来做什么"给出不同答案，以 `AGENTS.md` 为准。

## 1. 当前产品边界

stocks-claw 是个人投资分析师系统。当前生产仍使用确定性规则动作 + 受限 Outlook；
目标架构见 `ARCHITECTURE.md`（按组件标注 `[PRODUCTION]`/`[SHADOW]`/`[PLANNED]`/
`[DEPRECATED]`），当前要做的具体任务见 `docs/tasks/`，开发流程见 `AGENTS.md`。

- 系统不下单，不承诺收益。
- 用户是唯一决策人。
- 长期金融记忆只有在用户明确确认后才能写入。
- 行情、新闻、宏观、技术指标和 LLM 推断不得写入用户事实。
- 生产运行统一使用 `/mnt/user/code-project/stocks-claw` 的 `master`。

## 2. 当前操作入口

```bash
.venv/bin/python -m stocks.adapters.cli --output json
.venv/bin/python -m stocks.adapters.cli --scheduled-run-due
.venv/bin/python -m stocks.adapters.cli --scheduled-run-session cn_pre_close --force
.venv/bin/python -m stocks.adapters.cli --scheduled-run-latest cn_pre_close
```

资产和画像读取不需确认；写入必须使用 CLI `--confirmed` 或 MCP `confirmed:true`。

## 3. 当前生产报告规则（安全边界）

在 Advisory 主窗口切换（ROADMAP A5）前：

- 交易窗口产物仍以 `portfolio_decision.user_view` 为用户可见主结构；
- 主窗口可以附加已过滤 `outlook`，观察窗口可以附加 `outlook_delta`；
- Agent 不得从内部 action_cards、decision_id、position_id 或兼容字段自行构造动作；
- 数据异常、stale、fallback、锁定和结算边界必须展示；
- 研究候选不能进入交易指令卡；
- 风险暂停时不得把候选写成即时买入建议；
- Feishu 只使用加粗、行内代码、列表和链接，不使用表格、代码围栏、HTML 和标题符号。

旧 `LLMAnalysis` 和 archive prompts 不参与生产。

## 4. Advisory shadow 数据边界（安全约束，非开发流程）

职责如何划分（Provider/Engine/Rule/LLM/Validator/Renderer/Push）是架构问题，见
`ARCHITECTURE.md`；本节只列运行时不可违反的安全边界。

### 4.1 Shadow-first（数据边界，不可绕过）

当前 Advisory shadow 链路：

- 不进入生产 `user_view`；
- 不推送；
- 不自动保存 advice/execution/forecast；
- 只写 `.local/advisory_shadow/`；
- 必须能按 `run_id` 与当前规则路径对比。

### 4.2 LLM 输出约束

LLM 不得：

- 编造持仓、价格、新闻、来源和金额；
- 使用未授权标的；
- 把 candidate signal 当作必须采纳；
- 建议操作锁定、不可交易或非开放期资产；
- 绕过 data quality 和风险暂停；
- 直接持久化用户记忆。

LLM 必须：

- 逐条说明对重要规则候选的采纳、修改、推翻或延后；
- 引用 fact/evidence refs；
- 给出执行与取消条件；
- 区分今日动作、持有决策和研究候选；
- 标注数据限制和置信度。

### 4.3 Validator 约束

Validator 可以拒绝、警告、计算确定性金额或要求 LLM 修正一次；不得静默把动作 A
改成动作 B。仍无法通过时输出 `review_required`。当前独立 validator 模块的实现
状态见 `ARCHITECTURE.md` §5.6（不要假设 `advisory_validator.py` 已存在）。

## 5. 金融记忆写入规则

自然语言资产入口的用户可见入口点还不存在（见 `ARCHITECTURE.md`）。在它落地前，
外部 Agent 可以帮助用户组织资产写接口，但必须：

1. 先列出将新增、修改、删除的字段；
2. 对代码、币种、成本、数量、账户和流动性不确定项提问；
3. 获得明确确认；
4. 写入后读回验证；
5. 不根据行情或推断修改用户持仓事实。

## 6. 数据质量处理顺序

所有分析必须读取 `data_quality`。正确顺序：

```text
数据异常/缺失
→ 阻断相关推断
→ 使用可用的其他证据
→ 降低置信度或 review_required
```

不得把跨市场 stale、复权异常、基金 T-1 净值、手工估值或代理 ETF 当作同一种实时
证据。

## 7. 开发流程与验证

不在本文重复。开发流程、当前任务、文档权威顺序、session 纪律和 commit/push
规则见 `AGENTS.md`；验证命令见 `README.md`。
