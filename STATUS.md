# STATUS.md

## 并行修改：MCP 协议服务器（用户直接指令，未提交）

2026-10-19。原 `stocks.adapters.mcp` stdio 循环不是 MCP 协议（无 initialize/tools/list/
tools/call），标准客户端无法握手。新增 `stocks/adapters/mcp_server.py`：换行分隔
JSON-RPC 2.0、协议版本协商、`inputSchema` 与只读/破坏性标注、工具失败映射为
`isError` 结果、stdout 仅协议消息、引擎首次调用时才构建；`--read-only` 隐藏并拒绝全部
金融记忆写入工具。`python -m stocks.adapters.mcp` 改为委托该服务器。工具语义与
`confirmed=true` 确认门未改变。

- 验证：`tests/test_mcp_server.py` 13 项（含真实子进程握手）；官方 Python MCP SDK
  1.9.4 与 2.3.0 客户端握手、列出 23 个只读工具、调用 `financial_intake_template` 成功，
  只读模式下写工具被拒。全量 pytest **1934 passed / 7 skipped**；变更文件 Ruff、
  compileall、`git diff --check` 通过。
- 未做：NAS 部署、Hermes 实际接入、写入工具经 MCP 的真实调用；Dockerfile 远程绑定/
  健康检查未处理（NAS 走 uv venv，不依赖 Docker）。

## 并行修改：信息聚合中心正确性修复（用户直接指令，未提交）

2026-10-19。按“个人信息聚合 + LLM 判断辅助”定位裁剪审查清单后修复：

- 金融记忆写入改为原子写（`stocks/engine/atomic_io.py`）：资产 v1/v2、迁移、forecast、
  确认事件。中断不再留下半截文件。
- 交易日历：`load_scheduled_config` 合并 `exchange-calendars`（XSHG/XNYS）休市日与手填
  假日；补上原配置漏掉的 2026-01-02 A 股休市；超出库覆盖期（A 股当前到 2026-12-31）记录
  警告。静态事件日历到期同样警告（当前最后一条 2026-12-10）。
- 情报代理映射删除 KWEB→512480、IWM→512890、TLT→SGOV、SLV→NEM（目标均为实际持仓，
  会把不相关情报挂到错误持仓）。
- 部署脚本统一 `STOCKS_CLAW_REPO_ROOT`；小时巡逻改用仓库内 `intelligence_brief.py`（不再调用
  `/opt/data/scripts` 副本），并修复 `set -e` 下失败信息不输出；`pyproject` 新增可选
  `cn` extra（AkShare），`uv.lock` 仅新增依赖。
- 复核后不改：Polygon 回退报价带前一日时间戳，已被新鲜度判为 stale；盘中分钟级新鲜度在
  每次运行实时取价的前提下无实际触发场景。
- 验证：全量 pytest **1941 passed / 7 skipped**；变更文件 Ruff、compileall、
  `git diff --check`、`uv lock --check` 通过；`cli --output json --no-news --no-quotes` 实跑成功。

## 并行修改：投资日报为主输出（用户直接指令，未提交）

2026-10-19。定位为个人信息聚合 + LLM 辅助判断；主输出为投资日报，确认链建议路径保留。
用户要求日报不暴露资产明细：不显示金额、数量、总资产、权重、浮盈亏数值及非行情资产。

- 实测问题：一次完整上下文仅 10 条新闻（`fetch_news` 默认上限），定向新闻只覆盖静态
  watchlist，实际持仓零条专属新闻；历史预热 66 个标的串行约 2 分 18 秒。
- 修复：定向新闻与公告覆盖 watchlist ∪ 行情估值持仓并区分“持仓/关注”；每标的 3 条、总数 40、
  丢弃 7 天前旧闻（`engine.yaml news`）。历史预热限流并发（4）。
- 历史缓存保留期 90→180 天：长假后 90 个自然日不足 60 个 A 股交易日且缓存“够 80% 不重拉”，
  MA60 持续缺失；现有缓存在下一次过期重拉后恢复。
- 新增 `stocks/engine/investment_brief.py`：影响新闻、近期日历、持仓操作三部分及 LLM 判断。
  模型输入仅含仓位档位与浮盈亏档位、技术位置、新闻、日历、宏观数值，不含金额；输出校验
  链接/持仓/动作枚举，失败时保留新闻与日历并注明不可用。入口：`StocksEngine.investment_brief`、
  CLI `--investment-brief [--no-llm]`、MCP `investment_brief`、
  `scripts/cron/stocks-claw-push-investment_brief.sh`。
- `LLMClient` 新增可选流式（默认关闭，日报开启）：模型代理对超过 30 秒的非流式请求返回 502。
- 实测（本机，真实持仓只读、在线数据、DeepSeek 流式）：约 55 秒，10 个持仓均给出判断，
  输出中无金额、仓位百分比或浮盈亏数值。
- 新闻精确匹配：标题须含本代码，或（A 股）不含其他基金代码且含完整名称；美股基金须含 ticker，
  个股须含 ticker 或公司名；`news.blocked_sources` 屏蔽社区帖/标题党；每标的先取 5 倍候选再筛。
- 交易员指标 `stocks/engine/trader_metrics.py`：均线排列与斜率、RSI、MACD 柱变化、5/20/60 日涨跌、
  相对沪深300/标普500 强弱、ATR 与 2×ATR 止损、波动率、布林 %B 与带宽分位、量能趋势、OBV、
  20/60 日高低与回撤；市场环境含宏观、A股/美股广度、强弱板块。LLM 按资深交易员框架输出
  动作、怎么做、关键价位（支撑/阻力/止损/风险收益比）与依据；明确告知估值/资金流等数据缺失。
- 修复美股历史重复交易日：供应商日 K 的 UTC 零点换算到纽约时区落在前一天，与当日实时快照
  未去重，均线重复计算收盘价；零点时间戳改按 UTC 日期归属。历史预热 60→120 根。
- 实测：首轮因预热扩展重拉约 3.5 分钟，之后取数约 24 秒，含模型约 1.5-2 分钟；输出无金额、
  仓位百分比或浮盈亏数值；之前的社区帖与其他同类基金新闻不再出现。
- 已知局限：LLM 判断未做长期效果评价；无估值、资金流、基本面数据。
- 验证：全量 pytest **1955 passed / 7 skipped**；变更文件 Ruff、compileall、`git diff --check` 通过。

## 当前状态：HARDEN-04 在线数据与市场研究样本验收完成，保留外部缺口

2026-10-19。[当前任务](docs/tasks/TASK-HARDEN-04-online-data-market-acceptance.md)；
[详细在线清单、修复与人工核对](docs/analysis/online-market-acceptance-2026-10-19.md)。
提交基线仍为已推送的 `887084a9881418eea3385e8666840500f2a44952`。
HARDEN-03与本阶段HARDEN-04均留在工作区，未提交/推送；此前独立Hermes/设计修改保留。

### 实测结果与边界

- 38项在线检查：30返回样本、2停用、2空样本、1失败、2缺凭据、1按用户要求跳过。
  东方财富行情仍失败、历史复查恢复；Polygon/GNews未配置，SEC用户选择跳过。
  CSRC旧RSS无效停用，AkShare财报日历空样本，社融最新2026-04已超龄排除。
- 腾讯/Finnhub等实际取得公开行情，历史技术指标、新闻、国内/海外宏观可进入市场研究。
  修复DXY错误映射、国内指标字段、SAFE中间价来源与单位、RSS静默失败和统计日期丢失。
- 中美真实模型市场研究最终样本通过结构/引用契约和主要数字人工核对；没有账户动作。
  对实测出现的无账户配置指令和期货误称现货加入拒绝门禁，原响应保留。
  门禁不是通用事实验证器，时段措辞等残余问题见报告，仍需审阅。
- 情报最终8条有效输入→5个事件簇/0个信号，真实LLM响应；修复旧新闻/重复、引用与来源时间。
  情报报告服务模型名改为在线确认的 `glm-5.3-flash` 并取得真实响应。
- 所有公开运行在仓库外隔离目录，无真实金融资料读取/写入、消息、订单、cron或NAS变更。
  公开市场辅助研究可用不等于所有源健康、模型结论全对、个性化建议或生产交付验收通过。

### 验证

- 本轮所测快照全量pytest：**1921 passed / 7 skipped**，`/tmp/stocks-harden04-pytest-final.log`。
- 修改Python文件Ruff通过；最终全仓40条非本轮诊断（原39条，加并行MCP修改引入的unused json）。
  收尾时发现其他工作新增mcp_server.py/test_mcp_server.py；原样保留，以上测试计数不为其背书。
  compileall、diff检查通过。
- 在线完整轮、定向市场/情报模型复测、报告模型响应、SAFE解析5项定向测试完成。
- 新增可复现 `scripts/check_online_market.py`，命令退出成功不代表所有检查项健康。

### 后续输入

当前无需用户提供账户资料即可使用公开市场研究；SEC按用户选择保留缺口。
若要启用Polygon/GNews再配置对应凭据。真实账户/费用、组合投影及生产交付继续单独验收。

## 上轮结果：HARDEN-03 确认费用预算机制完成，真实费用与账户仍待用户资料

2026-10-19。用户明确授权提交推送 HARDEN-02 并继续实现不依赖真实资料的费用机制。
HARDEN-02 已推送到 `origin/master`：`887084a9881418eea3385e8666840500f2a44952`。
本轮任务为 [TASK-HARDEN-03](docs/tasks/TASK-HARDEN-03-confirmed-fee-budget.md)，实现留在工作区，
尚未提交/推送；此前独立的 Hermes/设计修改原样保留。

### 本轮实现与边界

- 新增 [费用预算契约](docs/contracts/fee-budget-v1.md)：账户、精确标的、市场/币种、有效期、
  数量/本金上限、单笔全部费用上界和来源。无默认费率或零费用；不模拟券商计费公式。
- CLI/MCP 支持只读预检/读取与用户确认后完整替换，空列表撤销全部预算；内容哈希和确认事件校验。
- 预算随上下文/统一快照冻结。已有持仓加仓按十进制校验购入本金加费用不超过确认可用现金。
- 新标的 buy、同账户同币种多笔买入、需要组合分桶/上限投影的动作继续阻断，不自动缩量或改变原提案。
- 报告展示费用预留及含费现金需求，验证展示一致性并在交付时检查预算到期。
- 预算不是资金锁定或实际费用；修改预算/成交后须重新分析，历史产物不会回写。跨运行资金占用与
  排队产物实时失效仍属于后续运行/交付门禁。本轮未授权或执行任何真实交易。

### 本轮验证

- 全量 pytest：**1907 passed / 7 skipped**，日志 `/tmp/stocks-harden03-pytest.log`。
- 本轮修改 Python 文件 Ruff、`compileall stocks tests scripts`、`git diff --check` 通过。
  全仓 Ruff 保留 **39 条既有诊断**，日志 `/tmp/stocks-harden03-ruff.log`。
- 实际 `smoke_fee_budget.py`：模拟确认预算经真实调度、快照、提案校验到确定性报告，
  覆盖费用充足、现金不足、撤销后新运行、交付到期阻断；未经确认写入为 0，分析不改金融事实。
  日志 `/tmp/stocks-harden03-smoke.log`。测试结果包含此前独立未提交测试。
- 未使用真实账户/费率、在线模型或真实行情，未部署、修改 cron、发送消息或写入真实金融记忆。

### 下一阶段

下一项代码缺口为整批组合约束与现金投影，之后是候选—持有—退出流程及运行交付门禁。
真实账户资料由用户稍后提供；届时需确认费用适用账户、标的、币种、规模范围、有效期和全部费用上界来源。
当前无需用户提供或确认具体费用；模拟通过不代表 L2-L5 验收。

## 上轮结果：HARDEN-02 账户只读预检与证据时间加固完成，真实账户验收待资料

2026-10-19。本轮任务为 [TASK-HARDEN-02](docs/tasks/TASK-HARDEN-02-account-readiness-evidence-time.md)。
当前提交基线 `fbdf03c2da8d0a14a0b4e1184653bf20000475f9`（HARDEN-01 已推送）；
HARDEN-02 已在后续用户授权下提交推送为 `887084a`。此前独立的 Hermes/设计修改继续保留。

### 本轮实现与边界

- CLI/MCP 新增单账户资料模板、只读预检和就绪检查；返回具体错误与核对差异，不自动修账或导入。
- 区分历史核对通过、当前事实可用与动作批准；账单过期/未来时点、缺少可用现金或数量、
  未确认画像和约束均显式列出。MCP `agent_status` 同步这些缺口。
- 快照缺失/无效时间、超前超过 60 秒或超过 90 分钟时阻断模型调用；宏观字段只用自己的来源时间。
- 新增 [接入说明](docs/contracts/account-intake-readiness-v1.md) 与 A 股/美股空模板；未知值保留 null。
- 尚无真实资料导入、确认写入、NAS 部署、cron 修改或消息发送。预检不是新批量写入器，也不授予建议权限。

### 本轮验证

- 全量 pytest：**1880 passed / 7 skipped**；日志 `/tmp/stocks-harden02-pytest.log`。
- 本轮变更 Python 文件 Ruff 通过；全仓仍有 **39 条既有诊断**，日志 `/tmp/stocks-harden02-ruff.log`。
- `compileall stocks tests scripts`、`git diff --check` 通过。
- 实际运行 account-readiness 与 advisory-lineage 两条隔离 smoke 均通过；A 股/美股预检、
  过期与跨账户阻断、只读金融文件哈希一致、确定性报告校验通过。
  日志 `/tmp/stocks-harden02-account-smoke.log`、`/tmp/stocks-harden02-advisory-smoke.log`。
- 模拟账户、行情、模型不能代替真实账户、在线模型或生产交付验收；测试包含此前独立未提交测试。

### 下一步及用户输入

完成 L2 真实账户核对需用户提供一个账户的两期完整账单（含可用余额/数量）、区间成交及实际费用、
非交易现金变动，并分别确认资产路由、画像和约束。先只读核对展示差异，再请求确认写入；不需要密码。
后续代码阶段依序为确认费用预算、整批组合约束投影、候选—持有—退出流程、运行交付门禁及效果评价。
买入/加仓费用和组合投影阻断仍保留，不因预检通过而解除。

## 上轮结果：HARDEN-01 分析与交付边界加固完成，整体未达到生产验收

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

## 上轮上下文（2026-10-18）：低频左侧交易模式方案，未部署 NAS

2026-10-18。当前工作是在既有运行模型上补齐用户适配方案和验收标准；没有写入真实金融记忆、
没有部署 NAS、没有恢复 cron。核心模式是低频扫描、左侧候选、core/tactical 分离、持有跟踪
和退出复核。当前 HEAD 为 `60b756a`，本轮文档和此前 Hermes deterministic 草稿改动尚未提交。

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
- 现有模拟建议仍可能把低置信度、证据不足的动作显示为 approved；在真实账户前必须先实现
  候选队列、core/tactical 角色、持有 thesis 和退出复核门禁。
- A 股与 IBKR 美股必须按市场日历、币种、结算和账户路径分别核对，不能共用一个泛化买卖信号。

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
