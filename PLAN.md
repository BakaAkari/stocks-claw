# stocks-claw 开发主线计划

> 版本:v4.3(2026-07-06,S3 定时扫描与触发推送工程完成)
> **现行准则只有两份:本文档(方向、规则与状态)+ `EXECUTION_PLAN.md`(任务与验收)。**
> 北极星是 `stocks/VISION.md`(v2.0);现状描述文档为 `ARCHITECTURE.md`、`stocks/DATA_MODEL.md`、`AGENT_GUIDE.md`(只描述现实,不含路线)。
> `docs/archive/` 下的一切(含 2026-07-03 归档的 `PLAN_v3.0_20260703.md`、`EXECUTION_PLAN_20260703.md`)无现行效力,仅作历史与参考。
> 文档与代码冲突时,以 grep/读码验证的代码现状为准,并回头修正文档。

---

## 1. 定位(2026-07-03 用户裁决)

本项目从"Agent-first 金融上下文工具包"升级为**个人投资分析师系统**:确定性系统承担工作台与记忆(数据、台账、触发、节奏),LLM/用户 Agent 承担当班分析师,用户是唯一决策人。完整定位与六个分析师能力域见 `stocks/VISION.md`。

同日裁决:废除旧红线"不为平台化提前做重型架构",替换为成长规则——**复杂度必须由已验证的价值拉动**(§3)。自动交易与收益承诺仍永久禁止。

## 2. 当前真实状态(2026-07-06 文档清理复核)

> 2026-07-03 以前的完成证据见归档件 `docs/archive/EXECUTION_PLAN_20260703.md`。
> S1/S2 的完整完成记录见 `docs/archive/EXECUTION_PLAN_S1_S2_20260706.md`;
> 当前行动入口见 `EXECUTION_PLAN.md`。文档与代码冲突时仍以读码、grep 与测试结果为准。

- **已完成(历史证据见归档件与 EXECUTION_PLAN 完成记录)**:P0 安全、P1 静默错误六项、P2 记忆回路、P3 删减、P4 数据底盘一期、P5 文档收口、M 建议闭环、F0、Phase D、Phase F 重新验收、G0 DecisionEnvelope 契约冻结、**切片 1 建议闭环**、**切片 2 资产数据结构 v2 与止盈止损基建(S2-0~S2-7+S2-E)**、**S2.5 受控扫描池扩容**、**切片 3 定时扫描与触发推送工程实现(S3-1~S3-5)**。当前契约:**AnalysisContext v12、data_quality v10、ScheduledAnalysisRun v1**。
- **当前验收闸**:S3-E 真实试运行。目标是在 A 股与 IBKR 美股 session 中连续运行一段时间,检查产物是否准时、数据边界是否清楚、Agent 二次分析是否符合用户风格,再决定是否加厚通知渠道、反馈记录或估值层。
- **未立项候选**:基金净值与贵金属报价 Provider、估值数据层、论点笔记本、组合归因、危机预案、分批执行计划、DecisionPlan 引擎化与内部 LLM 双路径。G0 契约保留为休眠资产,在 DecisionPlan 相关切片立项时接线,期间不得删除。
- **两起必须记住的事故**:①执行 Agent 伪造带 commit hash 的完成记录并勾掉物理上不可能通过的验收;②文档维护方曾未比对内容整体覆盖完成态清单。§6、§7 的规则由此导出,永久有效。

## 3. 路线原则:价值拉动的切片开发

蓝图不排期,反馈排期:

1. 开发以**垂直切片**推进:每个切片在 1~2 周内让用户在真实对话中获得一项新的、可感知的能力。
2. 切片出口除工程验收外,必须包含**用户价值裁决**:用户在真实使用后回答"是否有用、哪里不对、要不要加厚",结论写入 §9 决策日志。
3. 任何能力域先立**最薄版本**;加厚的唯一许可来源是决策日志中的用户裁决。没有裁决,再完善的设计也停在 backlog。
4. 切片之间无预设串行链。下一个切片由最近一次用户裁决与当时的实际瓶颈决定。
5. "先诚实,再博学"不变:数据不可信显式报缺;能力不可用返回机器可读的 `setup_required`/`no_data`;禁止任何路径静默降级成原始数据汇总。

## 4. 能力地图(方向,不是排期)

| 能力域 | 最薄版本 | 加厚方向(需用户裁决) |
|---|---|---|
| 复盘问责 | 执行记录 + 建议/触发/预测对照(切片 1) | 组合归因、行为模式复盘 |
| 可问责预测 | 预测台账 + 到期结算(切片 1) | 情景树、预期差、置信度校准 |
| 信息沉淀 | 论点笔记本 JSON(未立项) | 财报季工作流、宏观状态机 |
| 机会窗口 | rotation/action_signals + 事件/公告日历(短中线,已具备) | 估值层(长线)、一致预期与预期差 |
| 调仓操作 | 建议 actions 带幅度/触发/证伪(切片 1) | 分批计划、账户/费率约束、危机预案 |
| 主动节奏 | 轻量定时 session + `.local/scheduled_runs/` 产物 + Agent handoff(S3 已具备) | 早报/周报、事件临近提醒、通知渠道与反馈台账 |

## 5. 现行路线

### 已关闭:切片 1 建议闭环

内容:最小持仓映射 → 结构化建议 actions → 执行记录 → 预测台账 → 复盘注入。

状态:S1-E 已由用户验收关闭。工程闭环有效,但用户裁决指出资产入口粒度不足,因此进入切片 2。

### 已关闭:切片 2 出口(S2-E)

内容:资产数据结构 v2、逐持仓估值/PnL、暴露聚合、流动性护栏、建议粒度、成本基准触发器。

状态:S2-E 已由用户真实盘中试用关闭。用户反馈表明系统能正确围绕真实持仓、实时行情、持仓成本、浮盈浮亏、弱项跟踪与候选轮动输出可用建议;工程复核显示关键上市持仓已具备 `quantity + cost_basis`,真实 `build_context` 能输出逐持仓估值、暴露、流动性与建议粒度。

### 已关闭:S2.5 受控扫描池扩容

内容:扩展 `stocks/config/sector_scan.json`,覆盖 A 股宽基、成长、周期、消费、防御与港股主题 ETF 代理;每个方向先选高流动性代表 ETF,总量控制在 A 股扫描项约 30~40 个。

状态:已完成。扫描池共 50 项,其中 A 股/港股代理 32 项、美股 18 项;不做全市场扫描,不新增 `hk` 市场。真实带行情 CLI smoke 证明历史回填 70/70 可用、rotation/action_signals 均为 ok、rotation missing=0。实现中发现 `515880` 通信 ETF 因 2026-07 份额拆分污染历史收益,已替换为 `159695` 并增加配置守门测试。

### 已关闭:切片 3 定时扫描与触发推送(S3 工程实现)

内容:按 A 股与 IBKR 美股交易时段生成结构化运行产物,由 Agent 读取最新产物并二次分析/推送。设计参考 `docs/archive/SCHEDULED_ANALYSIS_CROSS_MARKET_DESIGN_20260706_zh.md`。

状态:工程已完成。新增 `scheduled_sessions.json`、`scheduled_analysis.py`、CLI
`--scheduled-run-due/--scheduled-run-session/--scheduled-run-latest`、幂等
`ScheduledAnalysisRun v1` JSON/Markdown 产物、Agent 必答任务、通知建议策略与测试。
第一版仍不做自动交易、不得自动写长期建议/执行/预测,不引入重型服务化依赖。

### 当前闸门:S3-E 真实试运行

内容:用真实 A 股与 IBKR 美股 session 运行一段时间,验证定时产物是否能稳定支撑
"盘前/开盘后/收盘前/盘后"分析,并记录需要微调的文本、触发阈值、推送策略和候选池问题。

状态:待用户试用。工程 smoke 证明功能可跑,但跨多天准时性、夜间打扰策略和 Agent
二次分析风格必须由真实使用反馈关闭。

### 切片候选队列(未排期,禁止开工)

基金净值与贵金属报价 Provider、估值数据层(A股/美股指数 PE/PB 百分位,解锁长线判断)、论点笔记本、组合归因、危机预案、分批执行计划、DecisionPlan 引擎化与内部 LLM 双路径(原 G1~G7;G0 契约已落盘休眠;2026-07-03 双路径裁决保留为方向,排期后移)。

## 6. 禁止事项

- **禁止虚报完成:完成记录必须附可复现证据(grep 命中行号、测试名及断言),只写 commit hash 无效;勾选物理上未验证的验收项视为最严重违规。**
- 禁止开工候选队列/backlog 内容;禁止跳过任务顺序自行挑活;禁止重构与当前任务无关的代码。
- 禁止新增 .md 文件(未在决策日志登记理由前);分析/调研产物一律进 `docs/archive/`。
- 禁止变更 `AnalysisContext`/`AdviceRecord` 等契约而不三处同步(`stocks/DATA_MODEL.md` + models/builder + schema 断言测试);schema 版本只增不减。
- 禁止在缺数据时伪造指标;缺数据显式 `no_data`;禁止收益承诺;最终判断权归用户与其 Agent。
- 禁止让默认 `pytest` 失败;默认测试不得访问真实网络(autouse 守门);禁止删除或跳过测试来让其通过。
- 禁止无对应切片裁决引入重型依赖(FastAPI/SQLAlchemy/Redis 级别);禁止提交 `.local/`、`.secret/`、缓存、快照、虚拟环境;禁止任何文档/代码/日志出现真实 API Key。
- 长期记忆(资产/画像/建议/执行/预测)只在用户确认后写入。

## 7. Agent 执行协议

1. **读**:本文档 → EXECUTION_PLAN 使用说明 → 认领的任务。
2. **验**:执行任务内所有【验证前提】;不成立 → 记 `> 跳过:<原因>`,不改代码。
3. **做**:只改任务列出的文件;需动其他文件先在任务下追加说明。
4. **收**:跑全局验收 → 勾选 → 追加 `> 完成:<commit> <说明> | 证据:<grep 行号/测试名及断言>` → 提交注明任务编号。
5. **改文档**:修改 PLAN/EXECUTION_PLAN 前必须先读取当前版本逐行比对,只做增量修改,禁止整体覆盖;**主线文档每次修正后立即 `git add + commit`**。
6. **停**:任务与代码矛盾、需改清单外文件、想到"更好的方案"、涉及删用户数据/变更 schema/新增依赖——停止并报告。

## 8. 全局验收命令(每任务必跑)

```bash
uv run ruff check .
uv run python -m pytest -q
uv run python -m compileall -q stocks tests
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

## 9. 决策日志(方向级决定追加于此,一行一条:`日期:决定;依据`)

### 历史日志(继承自 PLAN v3.0,原文保留)

- 2026-07-02:全库审计,生成 EXECUTION_PLAN(P0-P5);裁决删除 LLMEnhancer、回测暂缓;给出全部文档处置判决。依据:文档+代码交叉审计。
- 2026-07-02:执行方完成 P0/P1/P3/P4/P5 并归档 17 份文档;打 tag v2.1-phase2-complete。
- 2026-07-02:独立核验发现 P2 组完成记录虚报(MCP 无写入方法、save_context 零调用、prompt 未接线、HTTP 无鉴权),回退 P2 为待办;tag 不作为完成依据;新增"完成记录必须附证据"与"禁止整体覆盖文档"规则。依据:对最新工作区代码的逐文件 grep 核验。
- 2026-07-02:用户已完成泄露 key 的作废轮换,P0 遗留的人工步骤关闭。
- 2026-07-02:一次 git restore 冲掉了未提交的文档修正(v2.4 + 核验标注),已重新写回;新增规则"主线文档修正后立即 git commit"。
- 2026-07-02:Phase M 细化为 M-1~M-5 追加进 EXECUTION_PLAN,与 P2 返工合并为单一执行队列(P2 为硬前置);不新建任何文档。依据:M 组三个任务直接依赖 P2 的写入模式/快照回路/金额脱敏。
- 2026-07-02:P2-1~P2-5 已补齐代码行号、测试名与全局验收证据,Phase R 代码核验收口;Phase M 成为当前唯一开发任务。依据:EXECUTION_PLAN P2 完成记录与全局验收。
- 2026-07-02:Phase M M-1~M-5 全部完成并通过端到端守门测试;当前无新的进行中阶段。依据:EXECUTION_PLAN M 组完成记录与最终全局验收。
- 2026-07-02:立即收尾:清理 4 个未追踪空目录(stocks/cli、stocks/tests/test_{adapters,engine,providers});推送本地领先的 16 个 commit 到 origin/master;删除虚报状态的 tag v2.1-phase2-complete,在 a64c83e 重打 v2.7-phase-m-complete 作为 Phase M 出口标记。依据:全局验收 ruff/compileall/pytest 312 passed/CLI smoke 全通过。
- 2026-07-02:采纳数据可靠性审计结论,启动 Phase D(数据可信底盘与冗余),任务卡追加为 EXECUTION_PLAN D 组;审计证据归档 `docs/archive/DATA_RELIABILITY_AUDIT_20260702.md`。依据:外部 GPT 审计 5 条结论经行号级独立核验全部成立(含"单 bar 指标仍报 ok"代码级复现),现场网络诊断实测 eastmoney 日 K 0/6、腾讯日 K 6/6、Yahoo 日 K/宏观全线 429。
- 2026-07-02:解禁暂缓项"美股第二行情源",并扩展为"美股/加密第二行情与历史源"(D1-2)。依据:Yahoo 从用户网络全线 HTTP 429,美股/crypto 历史回填与宏观主链路当前已断,冗余从改进项升级为必需项。
- 2026-07-02:新闻源澄清:GPT 审计所称"仅两源贡献"经实测为当次偶发(三个启用源现均有产出 30/100/42 条),不单独立项;结构性缺口(无一手公告/财报/监管源)由 D2 承接。依据:`.local/verify_data_sources.py` 第 5 节实测输出。
- 2026-07-02:用户裁决启动 Phase F(前瞻决策层):分析输出从"回顾式总结"转向"事件驱动的条件化预案"。四项改造:事件日历、板块轮动脚手架、建议触发闭环、决策导向 prompt 契约。依据:用户对实际分析输出的直接反馈——"有数据只分析,起不到实质意义"。
- 2026-07-02:用户裁决修订红线"禁止把技术指标包装成投资建议"→ 允许引擎输出规则化方向性 action signal,约束:reasons 必附指标事实、缺数据显式 no_data、无收益承诺、最终判断归用户与 Agent。同批:扫描池扩至 26 标的并增 pool 分层。依据:用户明确反馈"我就是要把技术指标包装成投资建议…你一定要给我建议"。
- 2026-07-03:最新 Phase F 独立复核撤销其完成结论(至少 10 failures、trigger_review 缺失、26 扫描池标的无历史、过期事件仍标 upcoming);执行顺序改为 F0 基线修复优先,禁止在坏数据/坏基线上继续堆决策层。
- 2026-07-03:用户裁决内部决策 LLM 默认开启,采用 internal_llm/agent_delegate 双路径交付,规划 Phase G(G0~G7)。依据:用户要求工具在对话当下给出提前布局/调仓方案,不再只返回数据汇总。
- 2026-07-03:F0 修复 UpcomingEvent 完整时点/精度/生命周期,AnalysisContext v7→v8、data_quality v4→v5。依据:已发生事件被当作"未来"会污染调仓预案。
- 2026-07-03:D0-4 删除 us 伪 fallback,us/crypto 显式空链并暴露 single_source 风险;data_quality v5→v6。依据:降级能力必须由独立 Provider 证明。
- 2026-07-03:D1-1 接入腾讯 A 股前复权日 K 备用源,回填逐项暴露降级链;data_quality v6→v7。依据:主源持续 RemoteDisconnected 时备用源必须实际接管。
- 2026-07-03:D1-2 以实测可用性优先:美股改接 Nasdaq 免 key 日 K(Finnhub candle 403、Stooq 反爬),Yahoo 为备;crypto 用 Binance 日 K 主源+实时 fallback。依据:目标是独立可接管的数据源,不是遵守候选名称。
- 2026-07-03:D1-3 宏观改为 FRED→Yahoo→static 逐字段合并,新增逐字段真实观测日与 CPI同比/失业率/联邦基金利率月度官方统计;AnalysisContext v8→v9,data_quality v7→v8。D1 真实出口历史覆盖由 30/37 升至 37/37;宏观主备源故障两向均有可复现证据。依据:某个 Provider 部分成功不应阻断其他字段补齐,滞后统计也不能冒充实时。
- 2026-07-03:执行方在文档重构进行期间完成 D2-1(财报日历 12h 缓存)、D2-2(SEC EDGAR/巨潮公告一手源,合法空集不伪造)、D2-3(持仓定向新闻 scope;AnalysisContext v9→v10、data_quality v8→v9)、Phase D 出口(10:34,431 passed)、Phase F 重新验收(rotation 37/37、accumulate r20≥2% 门槛)与 G0(DecisionEnvelope 契约冻结+本地校验器)。完成记录与证据见归档件;本条系重构合并时转录入案。依据:写回前 diff 逐行核对。

### 现行日志

- 2026-07-03:用户裁决定位升级为"个人投资分析师系统"(复盘/信息沉淀/预测/机会窗口/调仓操作/主动节奏六能力域,见 VISION v2.0);废除"不为平台化提前做重型架构"红线,替换为"复杂度必须由已验证价值拉动"。依据:用户对能力发散讨论的明确采纳——"只要最终能满足真实分析师的目标,红线可以松弛"。
- 2026-07-03:文档体系重构:旧 PLAN v3.0 与旧 EXECUTION_PLAN 整份归档(`docs/archive/PLAN_v3.0_20260703.md`、`docs/archive/EXECUTION_PLAN_20260703.md`,本条登记这两份新增 md);VISION 升 v2.0;本文件重写为 v4.0;EXECUTION_PLAN 重写为仅含现行任务;ARCHITECTURE/AGENT_GUIDE/README/DATA_MODEL 维持现状描述不动。依据:旧 PLAN §1/§3 状态章节与自身决策日志矛盾、F 组勾选语义失效、G 组承载已被推翻的蓝图路线,继续增量修补会让执行 Agent 在矛盾地层上跑偏。
- 2026-07-03:旧 G1~G7 不再按蓝图串行排期,转入切片候选队列;已落盘的 G0 DecisionEnvelope 契约/校验器保留为休眠资产,DecisionPlan 相关切片立项时接线。"内部 LLM 默认开启 + agent_delegate 双路径"保留为方向性裁决,排期后移至切片 1 价值裁决之后。立项**切片 1(建议闭环)**:最小持仓映射、AdviceRecord actions 结构化、执行记录、预测台账、复盘注入;出口含用户价值裁决(不许 Agent 代答)。依据:§3 价值拉动规则;切片 1 是实用性最短路径,且为归因/行为复盘/校准积累原料数据。
- 2026-07-04:新增资产入口数据结构优化调研稿 `docs/archive/ASSET_DATA_MODEL_REFACTOR_20260704.md`,仅作为未来切片设计参考,不改变现行契约。依据:用户提供脱敏个人资产事实材料,要求结合系统设计与愿景判断哪些事实应结构化、哪些应派生或舍弃。
- 2026-07-04:立项**切片 2(资产数据结构 v2 与止盈止损基建)**,任务卡 S2-0~S2-7+S2-E 并入 EXECUTION_PLAN;用户裁决三决策点:①新增 Position/Account dataclass,FinancialAsset 保留为 v1 兼容层;②加载 v1 只做内存映射不自动写回,迁移走用户确认的一次性命令;③废除 raw_prompt_input 金额区间脱敏,上下文使用真实金额(HTTP include_amounts 边界不变)。同批并入愿景审计导出的四个必要件:建议粒度推导(detailed/sector/fixed)、暴露代理映射(exposure_proxy.json)、成本基准触发器(pnl_pct_above/below)、持仓行情宇宙自动守门。**S1-E 未关闭前 S2 不得开工。**依据:资产入口重构设计文档与愿景对照审计,用户在真实资产复合结构(锁定保险/大类基金理财/单支证券)上的明确需求裁决。
- 2026-07-04:登记新增 md:`docs/archive/ASSET_ENTRY_REDESIGN_20260704_zh.md`(资产入口重构设计,中文,修订并扩展同日英文初稿)、`docs/archive/VISION_GAP_AUDIT_20260704.md`(愿景对照审计);EXECUTION_PLAN 追加 S2 组。依据:PLAN §6 新增文档登记规则。
- 2026-07-04:文档清理:将 2026-07-02 重启前旧仓库遗留的 17 份归档文档(stocks-* 9 份、LLM_QUANT_* 3 份、LLM_ENHANCER_ANALYSIS、DESIGN、DESIGN_GAP_ANALYSIS、ARCHITECTURE_V2、ARCHITECTURE_BOUNDARY_ANALYSIS、NAS_DEPLOYMENT)移入 `_to_delete/` 待用户删除,全文仍在 git 历史;未追踪的 `stocks/data/history/` 运行时残留一并移入。保留仍被本日志引用的归档件(DATA_RELIABILITY_AUDIT_20260702、PLAN_v3.0_20260703、EXECUTION_PLAN_20260703、ASSET_DATA_MODEL_REFACTOR_20260704)。依据:用户指令"清理无用过时文档,保持目录干净"。
- 2026-07-04:S1-E 用户验收关闭。用户确认已真实使用 S1 建议闭环功能:S1 能完成信息收集、分析反馈、结构化建议、执行记录、预测与复盘注入,但实际建议细粒度仍不足,无法充分结合真实资产结构、持仓成本、流动性、锁定资金和跨包装暴露,因此未满足"个人投资分析师"核心需求。裁决:S1 工程闭环有效但上游资产入口是当前瓶颈,下一阶段按既定 S2 执行"资产数据结构 v2 与止盈止损基建"。依据:用户真实使用反馈与 S1-E 工程闸 461 passed/CLI smoke 通过。
- 2026-07-06:新增 S3 候选设计稿 `docs/archive/SCHEDULED_ANALYSIS_CROSS_MARKET_DESIGN_20260706_zh.md`,将"定时扫描与触发推送(pull→push)"具体化为覆盖 A 股与 IBKR 美股持仓的跨市场定时分析。本文档仅作设计与冲突审计,不改变当前 S2-E 用户验收闸;S3 是否立项仍需用户价值裁决。依据:用户要求补齐美股/IBKR 交易时段并检查与现有愿景、计划是否冲突。
- 2026-07-06:文档清理:删除已被 `.gitignore` 忽略且无 git 跟踪的 `_to_delete/` 待删旧文档与历史缓存残留;将 S0~S2-7 完整完成记录归档为 `docs/archive/EXECUTION_PLAN_S1_S2_20260706.md`,根目录 `EXECUTION_PLAN.md` 收缩为 S2-E 当前闸门;给归档历史计划、旧执行清单、S2 前审计/设计稿补充 superseded/archived 横幅;同步 PLAN/README 的当前状态。依据:用户要求审查全部文档,该归档的归档、该删除的删除。
- 2026-07-06:登记"受控扫描池扩容"为 S2.5/S3 支撑候选:扩展 A 股宽基、成长、周期、消费、防御与港股主题 ETF 代理,每个方向先选高流动性代表 ETF;港股只用 A 股上市 ETF/QDII 代理,不引入 `hk` 市场;扩容目标是提高"下一个机会"覆盖面,不是全市场扫描。依据:用户盘中使用反馈显示 512480/512880 这类扫描池候选能产生有用建议,但当前 A 股覆盖偏窄。
- 2026-07-06:S2-E 用户价值裁决关闭,并立项 S2.5 受控扫描池扩容。真实使用反馈显示,系统已能围绕用户 A 股/IBKR 持仓、实时行情、成本基准、浮盈浮亏与轮动候选输出基本符合需求的盘中建议;工程复核显示 `AnalysisContext v12/data_quality v10`、23 条资产持仓、23 条逐持仓估值、9/9 关键上市持仓均具备 `quantity + cost_basis`,暴露/流动性/建议粒度与资产边界均可输出。下一步先扩展扫描池,再进入 S3 定时推送。依据:用户明确表示"现在系统返回的内容比较满足我的需求",并要求更新文档后执行下一阶段开发。
- 2026-07-06:S2.5 受控扫描池扩容完成。`sector_scan.json` 扩为 50 项,其中 A 股/港股代理 32 项、美股 18 项;覆盖宽基、成长、周期、消费、防御与恒生科技/恒生医疗/港股互联网代理;配置测试防止重复、unsupported market、watchlist 重复和 `hk` 市场混入。真实 smoke 发现 `515880` 通信 ETF 受份额拆分污染历史收益,因此替换为 `159695` 并写入排除测试。验收:`ruff` 通过、`pytest` 491 passed、`compileall` 通过、带行情 CLI smoke `history requested=70 failed=0,rotation items=61 missing=0,action_signals items=61`。下一候选切片转为 S3 定时扫描与触发推送。
- 2026-07-06:S3 定时扫描与触发推送工程实现完成。新增 `scheduled_sessions.json`、`ScheduledAnalysisRun v1`、文件产物存储、A 股/美股时区 session 日历、CLI runner、Agent handoff 任务、通知建议策略与配置/CLI 测试;产物写入 `.local/scheduled_runs/`,不自动交易、不自动写 advice/execution/forecast。验收:`ruff` 通过、`pytest` 498 passed、`compileall` 通过、真实 scheduled smoke 生成 `cn_pre_close` status=ok,latest 读回 23 条持仓核对和 19 条 action signals,重复运行保护返回 `skipped_duplicate`。S3-E 转入真实试运行验收,需要跨 A 股与 IBKR 美股 session 收集实际效果反馈后关闭。依据:用户要求提交推送并按文档规划直接推进 S3 开发。
- 2026-07-08:立项 `global_intelligence_watch` 切片设计:在现有 8 次持仓推送外新增每小时全量新闻/宏观情报刮削分析推送。决策点:①主新闻源用 Google News RSS+现有 RSS,一手补充 Finnhub Market News;②行情/宏观覆盖 VIX/原油/美债/黄金/比特币/美元指数,黄金/原油/美元指数优先用 GLD/USO/UUP ETF 代理;③FRED API 由用户单独提供 key,因免费档约 120 次/日,美债 10Y/2Y/美元指数改为每 4 小时集中抓取;④排除低价值时段:北京时间 02:00-07:00 跳过,实际每天运行 19 次;⑤不设置新闻噪音阈值,全量生成产物;⑥非持仓标的允许给出买入/卖出/观察建议;⑦数据保留 7 天在线、7-30 天归档、>30 天删除;⑧产物复用 ScheduledAnalysisRun v1,新增 IntelligenceHarvester/IntelligenceAnalyzer/NewsIntelligenceStore 组件,不修改 AnalysisContext 主结构与现有测试。依据:用户明确确认 6 个设计约束并要求直接写方案。
- 2026-07-08:S3-E 真实试运行验收关闭。用户试用两天后给出 5 项调整点已完成并穿越验收:市场焦点过滤、触发器显式输出、高亏损标注、市场状态摘要、after_close 复盘意图区分。同日立项 global_intelligence_watch 每小时新闻/宏观情报切片并将开工实现。依据:用户真实试用反馈、全局验收 ruff/pytest/compileall/CLI smoke 通过。
- 2026-07-09:六角度专业审查完成。实施四项P0修复(硬编码汇率→实时汇率、-10%中间止损档、信号横截面排序、多因子压力测试);产出开发方向建议书 docs/archive/DEVELOPMENT_DIRECTION_20260709.md,含三层(健壮性→分析深度→长期愿景)共12个方向项与6条新增架构不变式。验收:ruff/pytest 509/compileall/CLI smoke 全绿。依据:用户要求多角度专业分析并整理开发建议。
- (追加格式:`日期:决定;依据`)
