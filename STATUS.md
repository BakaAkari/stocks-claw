# STATUS.md

## 当前状态：DOC-01 运行模型与验收标准加固完成，代码与 NAS 均未变更

2026-10-18。当前任务为
[运行模型与验收标准加固](docs/tasks/TASK-DOC-01-operating-model-hardening.md)。
本轮只修订愿景、运行模型、架构、路线、契约索引和操作指南，没有修改 Python 代码、
没有写入真实金融记忆、没有部署 NAS、没有恢复 cron，也没有推送消息。当前 HEAD 为
`a384464`，工作区中的文档改动尚未提交。

### 本轮设计结论

项目采用 [Operating Model v1](docs/contracts/operating-model-v1.md) 作为可实施约束：

`confirmed facts → reconciliation → snapshot → proposal → validation → user decision → actual execution → review`

个人约束、账户事实和成交核对先于组合建议；建议失败时保留市场信息但阻断受影响的组合动作；
策略研究必须记录来源、假设、成本、适用范围、样本、基准和失效条件；模拟烟测、单次
LLM 调用和测试通过不能替代真实账户验收或 NAS 生产验收。

### 当前实现层级

- L0 文档与契约：本轮完成，已定义事实类别、运行模式、就绪状态、停止条件和 L0-L5 部署门禁。
- L1 本地模拟：TRUST-01 的金融核对和 LLM 责任链烟测已通过；使用模拟账户、行情和模型。
- L2 本地真实账户只读：尚未开始；需要用户确认并导入真实账户快照、成交和现金记录。
- L3 NAS shadow：尚未开始；不得因代码已推送而恢复 cron 或生产推送。
- L4/L5 受控建议/日常运行：尚未开始。

### 当前最重要的未决问题

- 旧 `.local` 画像或约束文件的存在不能自动证明用户确认，需补确认事件和内容哈希来源。
- `LOCAL`、`SHADOW`、`PRODUCTION` 生命周期与部署级别必须分开理解；合同索引已按此修订。
- 文档中旧阶段记录保留作历史时，不得继续作为当前任务或部署结论使用。
- 当前先录入并核对真实账户，再做 NAS shadow；没有真实核对证据前不实现胜率、自动调参或策略升级。

### 本轮验收

- `git diff --check` 通过。
- 仅文档变更；没有运行代码测试，因为本轮没有修改代码。
- 真实账户、在线 LLM、NAS shadow、最终推送和用户价值均未验收。

## 历史状态材料

旧阶段记录已移至 [docs/archive/status-history-through-2026-10-15.md](docs/archive/status-history-through-2026-10-15.md)。
历史材料只用于追溯，不定义当前任务、部署状态或生命周期标签。
