# stocks-claw 开发主线计划

> 版本:v3.0(2026-07-03,决策交付双路径与个人组合行动层规划)
> **现行准则只有两份:本文档(方向与边界)+ `EXECUTION_PLAN.md`(任务与验收)。** `docs/archive/` 下的一切无效力。
> 文档与代码冲突时,以 grep/读码验证的代码现状为准,并回头修正文档。

---

## 1. 当前真实状态(2026-07-03 独立核验,证据级)

Phase R(修复与收口)**已完成代码核验与证据收口**:

- **核验通过**:P0 安全(泄露 key 已从追踪文件清除,汇率缓存迁出 `.secret/`)、P1 全部六项静默错误修复、P3 删减(`llm_enhancer.py`/`llm_utils.py` 已物理删除、僵尸配置已清)、P4 数据底盘(财经新闻源、Finnhub typed errors、美股 stale 兜底)、P5 文档收口(17 份归档、根目录收敛至 6 份、新 `ARCHITECTURE.md`)。
- **P2 返工已收口**:P2-1(资产 CRUD 暴露)、P2-2(profile 写入接口)、P2-3(快照写入回路)、P2-4(advice prompt 接线)、P2-5(HTTP 鉴权)均已在 `EXECUTION_PLAN.md` 中补齐代码行号、测试名和全局验收证据。
- **Phase M 已完成**:AdviceRecord 数据结构、确认式建议保存接口、最近建议注入、建议表现回看与端到端守门测试均已落地,建议从"一次性输出"升级为"可追踪、可回看"。
- tag `v2.1-phase2-complete` 打在未完成状态上,不作为完成依据;处置(删除/重打)由用户决定。
- 两起需要记住的事故:①执行 Agent 伪造了带 commit hash 的完成记录并勾掉了物理上不可能通过的终局验收;②文档维护方(Claude)曾在未比对内容的情况下覆盖过完成态清单(已恢复)。第 4、5 节的规则由这两起事故直接导出。

**净结论:P2 记忆写入回路与 Phase M 的建议保存/表现回看已完成;Phase D 仅完成 D0-1~D0-3,D0-4/D1/D2 未完成。2026-07-03 对最新 Phase F 提交复核发现:全量 pytest 未通过、`trigger_review` 代码缺失、schema/旧契约测试未同步、扫描池 26 个标的均无本地历史、已发生事件仍会留在 upcoming_events。Phase F 因此降级为"组件已落盘但未通过出口验收",不得宣称完成。**

## 2. 定位与已裁决事项(不变,重申)

Agent-first 的个人金融数据与分析上下文工具包,北极星是 `stocks/VISION.md` 的 personal investment advisor;衡量进度的唯一标准是 VISION 的 5 条成功标准。

已裁决、不再重议:LLMEnhancer 已删除;重型回测/因子平台暂缓(但允许为 action signal 做轻量 walk-forward 结果校准);三级输出粒度/子命令 CLI 不做;MCP SDK 重写推迟到 Phase H(新增现有轻量 MCP 工具不等于 SDK 重写);自动交易永久不做。

决策交付采用两阶段模型:

1. **内部决策生成阶段**:内部 LLM 默认开启,输出受 schema 约束的 `DecisionPlan`,不直接下单、不拥有最终解释权。
2. **用户 Agent 最终分析阶段**:用户 Agent 必须读取并审查 `DecisionPlan`、原始证据与 data_quality,可采纳或有理由推翻,然后结合当前对话给用户最终完整分析。

内部 LLM 的凭据可用时直接执行;URL/key 缺失时工具必须返回机器可读 `setup_required`,让用户 Agent 提示用户配置,或切换 `agent_delegate`——由用户 Agent 按同一 DecisionPlan 契约完成内部决策阶段,经本地校验后再进入最终分析。任何路径都不得静默退化成原始数据汇总。

## 3. 阶段路线

### Phase R:已完成 P2 返工

P2-1 → P2-2(剩余项)→ P2-3 → P2-4 → P2-5 均已带可复现证据收口。`v2.1-phase2-complete` 是否删除/重打仍由用户决定。

### Phase M:已完成建议闭环

AdviceRecord 数据结构(schema v6)、确认式建议保存接口、build_context 注入上次建议、建议表现回看、端到端守门测试均已带证据完成。细节与验收以 EXECUTION_PLAN M 组为准。

偏好对话流标准化仍暂缓;组合风险最小集(组合波动率、最大回撤、HHI、候选相关性)已纳入 Phase G2,硬前置为 Phase D 出口通过——坏数据上不算风险指标。

### Phase D:数据可信底盘与冗余(未完成,仍是决策层硬前置)

原则:**先诚实,再博学**——数据不可信时显式报缺,绝不静默装好;在诚实的质量层之上再建数据源冗余。三段硬顺序:D0 可信度硬化(指标按 data_points 判级、全链路真实 as_of、回填失败显性上报、fallback 配置语义修正)→ D1 关键源冗余(A 股腾讯第二历史源、美股/crypto 第二行情与历史源、FRED 权威宏观)→ D2 第一方事件源(财报日历、SEC EDGAR/巨潮公告、持仓定向新闻)。任务、行号、验收一律以 `EXECUTION_PLAN.md` D 组任务卡为准;出口对照 VISION 成功标准第 4、5 条。

### Phase F0:前瞻层基线修复(当前第一优先级)

撤销 Phase F 的完成结论,先修复最新提交造成的默认测试失败、补齐真实 `trigger_review`、修正事件过期/时区、同步 schema/文档/旧契约测试,并保证测试不因扫描池加载而访问真实网络。F0 通过后才回到 D0-4。

### Phase G:决策产品化与个人组合行动系统(新增,Phase D 出口后执行)

把 `AnalysisContext` 从主要交付物降为证据层,新增稳定的 `DecisionPlan` 决策层。顺序为:持仓标的映射 → 组合动作与仓位幅度计算 → 可比较的机会评分 → 事件生命周期 → DecisionPlan schema/校验 → 内部 LLM 与 Agent delegate 双路径 → 触发检查与轻量效果校准。详细任务和验收以 `EXECUTION_PLAN.md` G 组为准。

### Phase H:交付硬化(仅当 NAS/HTTP/MCP 有真实稳定使用需求时)

HTTP 完整认证限速、MCP SDK 重写、health check 暴露降级状态、并发模型修复。

### 长期暂缓区

多 Agent 辩论、通用因子挖掘/因子库、重型回测框架、四层记忆系统、通用审计平台。(原暂缓项"美股第二行情源"已于 2026-07-02 经决策日志解禁并扩展为 Phase D 的 D1-2;组合相关性最小集与 action signal 轻量效果校准属于 Phase G 必需项,不再属于暂缓区。)

## 4. 禁止事项

- **禁止虚报完成:完成记录必须附可复现证据(grep 命中行号、测试名及断言),只写 commit hash 无效;勾选物理上未验证的验收项视为最严重违规。**
- 禁止实现暂缓区内容;禁止跳过任务顺序自行挑活;禁止重构与当前任务无关的代码。
- 禁止新增 .md 文件(未在决策日志登记理由前);分析/调研产物一律进 `docs/archive/`。
- 禁止变更 `AnalysisContext` schema 而不同步 `stocks/DATA_MODEL.md` + 本文档 + 测试。
- 禁止在缺数据时伪造指标。引擎允许输出规则化方向性动作信号(action_signals,2026-07-02 用户裁决),但每个信号必须附引用指标事实的 reasons、缺数据必须显式 no_data、不得输出收益承诺;最终判断权归用户与 Agent。
- 禁止把“LLM 已调用”视为决策完成:内部 LLM/agent_delegate 的输出都必须通过同一 DecisionPlan schema 与证据引用校验;校验失败只能显式 failed/degraded,不得回退成无结构长文冒充成功。
- 禁止在 URL/key 缺失时静默关闭内部决策能力:必须返回 `setup_required` 和安全配置指引,或由用户 Agent 明确选择 `agent_delegate`;日志、错误和上下文永不回显 key。
- 禁止引入重型依赖;禁止提交 `.local/`、`.secret/`、运行态缓存、快照、虚拟环境;禁止任何文档/代码/日志出现真实 API Key。
- 禁止让默认 `pytest` 失败;禁止删除或跳过测试来让其通过。

## 5. Agent 执行协议

1. **读**:本文档 → EXECUTION_PLAN 核验附记与使用说明 → 认领的任务。
2. **验**:执行任务内所有【验证前提】;不成立 → 记 `> 跳过:<原因>`,不改代码。
3. **做**:只改任务列出的文件;需动其他文件先在任务下追加说明。
4. **收**:跑全局验收 → 勾选 → 追加 `> 完成:<commit> <说明> | 证据:<grep 行号/测试名>` → 提交注明任务编号。
5. **改文档**:修改 PLAN/EXECUTION_PLAN 前,必须先读取当前版本并逐行比对,只做增量修改;禁止整体覆盖。**主线文档每次修正后立即 `git add + commit`**——未提交的文档修正已被一次 `git restore` 静默冲掉过,不允许再发生。
6. **停**:任务与代码矛盾、需改清单外文件、想到"更好的方案"、涉及删用户数据/变更 schema/新增依赖——停止并报告。

## 6. 全局验收命令(每任务必跑)

```bash
uv run ruff check .
uv run python -m pytest -q
uv run python -m compileall -q stocks tests
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

## 7. 决策日志(方向级决定追加于此,一行一条:`日期:决定;依据`)

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
- 2026-07-02:用户裁决启动 Phase F(前瞻决策层):分析输出从"回顾式总结"转向"事件驱动的条件化预案"。四项改造:①事件日历(官方已公布 FOMC/CPI/非农日程静态配置 + Finnhub 财报日历,新增 `upcoming_events`);②板块轮动脚手架(`sector_scan.json` 扫描池 + 历史收盘 5/20 日相对强弱,新增 `rotation`);③建议触发闭环(AdviceRecord 增可选 `triggers`,回看时按收盘价核对 fired/not_fired);④`personal_advice_prompt.txt` 重写为决策导向契约(情景树/触发清单/强制"下一个机会"/禁止无条件观察)。AnalysisContext v6→v7,data_quality v3→v4,DATA_MODEL 已同步。D2 的财报日历需求由 ①部分承接,D2 立项时应与 event_calendar 合并而非另建。任务卡见 EXECUTION_PLAN F 组。依据:用户对 2026-07-02 实际分析输出的直接反馈——"有数据只分析,起不到实质意义",要求系统在当下时间点给出提前布置方向。
- 2026-07-02:用户裁决修订 §4 红线"禁止把技术指标包装成投资建议"→ 允许引擎输出规则化方向性 action signal(accumulate_candidate/wait_for_pullback/reduce_risk/avoid_catching_falling_knife/rotation_candidate/neutral_hold/no_data + event_watch 叠加),约束:reasons 必附指标事实、缺数据显式 no_data、无收益承诺、最终判断归用户与 Agent。同批:扫描池扩至 26 标的并增 pool 分层(core/broad/sector/defensive/rates/ai_chain),Instrument 增 pool 字段,AnalysisContext v7 增 action_signals,data_quality v4 增 action_signals 节点。依据:用户明确反馈"我就是要把技术指标包装成投资建议…你一定要给我建议",无方向语义的输出对其无实用价值。
- 2026-07-03:最新 Phase F 独立复核撤销其完成结论:本机收集 383 tests,全量运行已出现至少 10 failures;Phase F 定向 40 tests 中 6 个 trigger_review 用例失败,`advice_review.py` 无触发核对实现;运行态 37 标的仅 10 个可排名、27 个 no_data(26 个扫描池均无缓存);已过 `time_utc` 的当日事件仍被标 upcoming。执行顺序改为 F0 基线修复 → D0-4 → D1 → D2 → G,禁止在坏数据/坏基线上继续堆决策层。
- 2026-07-03:用户裁决内部决策 LLM 默认开启,并采用双路径交付:凭据齐全走 internal_llm;URL/key 缺失时用户 Agent 必须提示安全配置或选择 agent_delegate,由用户 Agent按同一 DecisionPlan 契约生成中间决策;无论哪条路径,用户 Agent最后结合对话、证据和质量边界完成最终分析。新增 Phase G 规划持仓映射、仓位计算、机会评分、DecisionPlan、事件生命周期、触发监控和轻量效果校准。依据:用户要求工具在对话当下给出提前布局/调仓方案,不再只返回数据汇总。
- 2026-07-03:F0 修复 UpcomingEvent 的完整时点、精度与生命周期字段,属于破坏性契约变化,故 AnalysisContext v7→v8、data_quality v4→v5;已发生事件不再进入 upcoming/event_watch,过滤数量由 expired_count 显式上报。依据:同日事件发生后仍被当作“未来”会直接污染调仓预案,且 schema 红线禁止字段变化不升版本。
- 2026-07-03:D0-4 删除 us 指向 finnhub 自身的伪 fallback,us/crypto 显式空链,并由 quotes.by_market.single_source 暴露真实单源风险;data_quality v5→v6。真实出口验证 37 个历史标的 30 可用/7 缺失,质量节点与磁盘逐项一致,D0 完成后进入 D1。依据:降级能力必须由独立 Provider 证明,不能由配置键存在推断。
- (追加格式:`日期:决定;依据`)
