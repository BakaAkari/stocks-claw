# Agent 使用指南

> 本文供 AI Agent 和开发者操作当前系统，并约束下一阶段 Advisory 重构。

## 1. 当前产品边界

stocks-claw 是个人投资分析师系统。当前生产仍使用确定性规则动作 + 受限 Outlook；目标架构见 `stocks/VISION.md` 和 `ARCHITECTURE.md`，实施清单见 `EXECUTION_PLAN.md`。

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

## 3. 当前生产报告规则

在 A5 切换前：

- 交易窗口产物仍以 `portfolio_decision.user_view` 为用户可见主结构；
- 主窗口可以附加已过滤 `outlook`，观察窗口可以附加 `outlook_delta`；
- Agent 不得从内部 action_cards、decision_id、position_id 或兼容字段自行构造动作；
- 数据异常、stale、fallback、锁定和结算边界必须展示；
- 研究候选不能进入交易指令卡；
- 风险暂停时不得把候选写成即时买入建议；
- Feishu 只使用加粗、行内代码、列表和链接，不使用表格、代码围栏、HTML 和标题符号。

旧 `LLMAnalysis` 和 archive prompts 不参与生产。

## 4. 新 Advisory 实施规则

### 4.1 职责

- Provider/Engine：事实、计算和来源；
- Rule modules：候选信号和约束证据；
- LLM：InvestmentAdvisory 综合判断；
- Validator：证据与可执行性检查；
- Renderer：纯展示；
- Push：时效、receipt、完整性和渠道格式。

禁止在多个模块重复实现同一种语义验证。禁止通过全局裸数字集合授权金融声明。

### 4.2 Shadow-first

A0–A4 的新 Advisory：

- 不进入生产 user_view；
- 不推送；
- 不自动保存 advice/execution/forecast；
- 只写 `.local/advisory_shadow/`；
- 必须能按 run_id 与当前规则路径对比。

### 4.3 LLM 输出约束

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

### 4.4 Validator 约束

Validator 可以拒绝、警告、计算确定性金额或要求 LLM 修正一次；不得静默把动作 A 改成动作 B。仍无法通过时输出 `review_required`。

### 4.5 文件和 schema

- 新 schema 只增不减，必须同步 `stocks/DATA_MODEL.md`；
- 计划文件中的路径和接口为实施约束，变更前更新 `EXECUTION_PLAN.md`；
- 大文件新增功能优先拆到独立模块，不继续堆入 `scheduled_analysis.py`；
- 不提交 `.local/`、`.secret/`、缓存、快照和模型原始响应。

## 5. 金融记忆

自然语言资产入口完成前，外部 Agent 可以帮助用户组织资产写接口，但必须：

1. 先列出将新增、修改、删除的字段；
2. 对代码、币种、成本、数量、账户和流动性不确定项提问；
3. 获得明确确认；
4. 写入后读回验证；
5. 不根据行情或推断修改用户持仓事实。

## 6. 数据质量

所有分析必须读取 `data_quality`。正确顺序：

```text
数据异常/缺失
→ 阻断相关推断
→ 使用可用的其他证据
→ 降低置信度或 review_required
```

不得把跨市场 stale、复权异常、基金 T-1 净值、手工估值或代理 ETF 当作同一种实时证据。

## 7. 开发协议

1. 读 `VISION → PLAN → ARCHITECTURE → EXECUTION_PLAN`；
2. 只执行当前阶段；
3. TDD：先失败测试，再最小实现；
4. 每任务单独 commit + push；
5. 当前生产路径改变前必须有 shadow 和回退；
6. 文档与代码冲突时以当前代码为事实，同时修正文档；
7. 删除用户数据、修改凭证、切换生产路径或扩大长期记忆写入必须先说明后果。

## 8. 全局验证

```bash
.venv/bin/ruff check .
.venv/bin/pytest -q -o 'addopts='
.venv/bin/python -m compileall -q stocks tests
.venv/bin/python -m stocks.adapters.cli --output json --no-news --no-quotes
```
