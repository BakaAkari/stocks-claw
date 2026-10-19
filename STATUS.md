# STATUS.md

## 当前状态：HARDEN-01 分析与交付边界加固完成，整体未达到生产验收

2026-10-19。用户要求全面审查并增强系统；本轮完成的单一实现任务是
[TASK-HARDEN-01](docs/tasks/TASK-HARDEN-01-analysis-delivery-boundaries.md)。
[系统审查与后续实施顺序](docs/analysis/system-hardening-2026-10-19.md) 覆盖金融事实、
输入证据、建议、展示、调度、用户适配、评价和部署，区分代码缺口与真实验收缺口。

### 本轮实现

- `build_context` 不再自动结算、写回预测；历史金融记录只读，分析缓存/产物仍可保存。
- AnalysisContext 通过 `replace` 填充已声明的运行时字段，配置与引擎对象隔离。
- 四主窗口报告入口核对完整 Advisory 来源、快照/receipt/展示哈希、原提案和动作关联；
  缺失来源、跨窗口错配、重复或遗漏动作、改写内容均拒绝。
- 无模型/关闭模型保留明确的待复核封套，不恢复规则动作。
- 现有 cron 调用的 `run_llm_report.py` 对四主窗口采用确定性展示，保留上游 LLM 的分析；
  第二次自由文本改写不再触碰交易动作。主窗口手动重发也不能绕过时效。
- 缺少市场研判引用时只将 outlook 标为待复核，保留独立通过校验的持仓提案。
- Hypothesis 旁路失败保留日志与 artifact 错误；payload 在最终文本校验通过后才落盘。
- 修正文档中 MCP 入口未接入、主窗口未接 Advisory、观察窗口默认可用等过时描述。

### 本轮验证

- 全量 pytest：**1846 passed / 7 skipped / 0 failed**；日志 `/tmp/stocks-harden01-pytest.log`。
- 本轮变更 Python 文件 Ruff 通过；全仓 Ruff 仍有 **39 条既有诊断，分布在 13 个本轮未修改文件**，
  不宣称全仓静态检查通过；日志 `/tmp/stocks-harden01-ruff.log`。
- `compileall stocks tests scripts` 与 `git diff --check` 通过。
- 实际隔离 smoke：`scripts/smoke_advisory_lineage.py` 通过，新增最终确定性报告校验；
  未确认写入为 0、重复新增为 0、只读复盘通过。日志 `/tmp/stocks-harden01-smoke.log`。
- 模拟 smoke 不代表在线模型、真实行情、真实账户、NAS、飞书送达或投资有效性验收。

### 仍需完成

依次处理真实账户只读就绪与证据时点、确认费用预算、整批组合约束投影、
core/tactical 候选—持有—退出流程、部署/备份恢复门禁，最后才是有固定定义的效果评估。
买入/加仓的费用预算阻断和组合约束投影阻断仍保留，不能仅靠录入账户解除。
本轮没有导入真实账户、写入真实金融记忆、修改密钥、部署、恢复 cron 或发送消息。
用户已授权提交并推送 HARDEN-01；提交基线为 `60b756a`。此前独立的 Hermes/设计修改保留在工作区，
不纳入本轮提交。上述全量测试结果来自工作区，包含此前未提交的独立测试。

## 上轮记录：Hermes 唯一入口契约加固（历史快照）

2026-10-18。当前任务为
[DOC-02 确认来源与内容哈希加固](docs/tasks/TASK-DOC-01-operating-model-hardening.md)。
本轮在 DOC-02 基础上补齐 Hermes MCP 编排入口和失败保护；没有写入真实金融记忆、没有部署
NAS、没有恢复 cron，也没有推送消息。当前 HEAD 为 `86b4c9b`，本轮改动已提交，待推送云端。

### 本轮设计结论

项目采用 [Operating Model v1](docs/contracts/operating-model-v1.md) 作为可实施约束：

`confirmed facts → reconciliation → snapshot → proposal → validation → user decision → actual execution → review`

个人约束、账户事实和成交核对先于组合建议；建议失败时保留市场信息但阻断受影响的组合动作；
策略研究必须记录来源、假设、成本、适用范围、样本、基准和失效条件；模拟烟测、单次
LLM 调用和测试通过不能替代真实账户验收或 NAS 生产验收。

### 当前实现层级

- L0 文档与契约：已定义事实类别、运行模式、就绪状态、停止条件和 L0-L5 部署门禁。
- DOC-02：profile、constraints、资产账本、账户时点记录、现金变动和成交记录均使用内容寻址确认事件；旧文件或被篡改文件保持 `legacy_unverified`。确认事件封套本身也校验完整性、操作者和 CLI/MCP 渠道。
- L1 本地模拟：TRUST-01 的金融核对和 LLM 责任链烟测已通过；使用模拟账户、行情和模型。
- L2 本地真实账户只读：尚未开始；需要用户确认并导入真实账户快照、成交和现金记录。
- L3 NAS shadow：尚未开始；不得因代码已推送而恢复 cron 或生产推送。
- L4/L5 受控建议/日常运行：尚未开始。

### 当前最重要的未决问题

- 旧 `.local` 画像或约束文件的存在不能自动证明用户确认；当前确认事件已补齐，旧文件仍需通过真实账户导入流程重新确认。
- `LOCAL`、`SHADOW`、`PRODUCTION` 生命周期与部署级别必须分开理解；合同索引已按此修订。
- 文档中旧阶段记录保留作历史时，不得继续作为当前任务或部署结论使用。
- 当前先录入并核对真实账户，再做 NAS shadow；没有真实核对证据前不实现胜率、自动调参或策略升级。

### 本轮验收

- 相关测试：确认来源、金融服务、个人约束和金融适配器 **60 passed**（全量包含 1821 项通过）。
- 金融核对 smoke 通过，重复写入无新增，确认事件与内容哈希一致。
- 全量 pytest：**1821 passed / 7 skipped / 0 failed**；日志 `/tmp/stocks-doc02-final-pytest.log`。
- 变更文件 Ruff、compileall 和 `git diff --check` 通过；Advisory smoke 使用固定工作日避免休市日期造成测试漂移。
- 真实账户、在线 LLM、NAS shadow、最终推送和用户价值均未验收。

Hermes 入口加固：新增只读 `agent_status`，以及 `asset_intake_draft` / `asset_intake_confirm`；
资产迁移和草稿确认均绑定 MCP/CLI 来源。契约要求先读取状态，再录入、核对、生成建议、
记录决定与成交、最后复盘；失败必须停止并保留原始错误，不得用推断值绕过确认。相关定向测试
70 项通过，全量 **1822 passed / 7 skipped / 0 failed**；本轮代码和契约改动尚未提交。

## 历史状态材料

旧阶段记录已移至 [docs/archive/status-history-through-2026-10-15.md](docs/archive/status-history-through-2026-10-15.md)。
历史材料只用于追溯，不定义当前任务、部署状态或生命周期标签。
