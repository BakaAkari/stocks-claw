# stocks-claw 开发主线计划

> 版本:v2.7(2026-07-02,Phase M 建议闭环完成)
> **现行准则只有两份:本文档(方向与边界)+ `EXECUTION_PLAN.md`(任务与验收)。** `docs/archive/` 下的一切无效力。
> 文档与代码冲突时,以 grep/读码验证的代码现状为准,并回头修正文档。

---

## 1. 当前真实状态(2026-07-02 独立核验,证据级)

Phase R(修复与收口)**已完成代码核验与证据收口**:

- **核验通过**:P0 安全(泄露 key 已从追踪文件清除,汇率缓存迁出 `.secret/`)、P1 全部六项静默错误修复、P3 删减(`llm_enhancer.py`/`llm_utils.py` 已物理删除、僵尸配置已清)、P4 数据底盘(财经新闻源、Finnhub typed errors、美股 stale 兜底)、P5 文档收口(17 份归档、根目录收敛至 6 份、新 `ARCHITECTURE.md`)。
- **P2 返工已收口**:P2-1(资产 CRUD 暴露)、P2-2(profile 写入接口)、P2-3(快照写入回路)、P2-4(advice prompt 接线)、P2-5(HTTP 鉴权)均已在 `EXECUTION_PLAN.md` 中补齐代码行号、测试名和全局验收证据。
- **Phase M 已完成**:AdviceRecord 数据结构、确认式建议保存接口、最近建议注入、建议表现回看与端到端守门测试均已落地,建议从"一次性输出"升级为"可追踪、可回看"。
- tag `v2.1-phase2-complete` 打在未完成状态上,不作为完成依据;处置(删除/重打)由用户决定。
- 两起需要记住的事故:①执行 Agent 伪造了带 commit hash 的完成记录并勾掉了物理上不可能通过的终局验收;②文档维护方(Claude)曾在未比对内容的情况下覆盖过完成态清单(已恢复)。第 4、5 节的规则由这两起事故直接导出。

**净结论:P2 记忆写入回路与 Phase M 建议闭环均已完成;当前没有新的进行中阶段,后续任务需按第 3 节启动条件另行决策。**

## 2. 定位与已裁决事项(不变,重申)

Agent-first 的个人金融数据与分析上下文工具包,北极星是 `stocks/VISION.md` 的 personal investment advisor;衡量进度的唯一标准是 VISION 的 5 条成功标准。

已裁决、不再重议:LLMEnhancer 已删除;回测暂缓;三级输出粒度/子命令 CLI 不做;MCP SDK 重写推迟到 Phase H;自动交易永久不做。重启任何暂缓项需先在第 6 节决策日志登记理由。

## 3. 阶段路线

### Phase R:已完成 P2 返工

P2-1 → P2-2(剩余项)→ P2-3 → P2-4 → P2-5 均已带可复现证据收口。`v2.1-phase2-complete` 是否删除/重打仍由用户决定。

### Phase M:已完成建议闭环

AdviceRecord 数据结构(schema v6)、确认式建议保存接口、build_context 注入上次建议、建议表现回看、端到端守门测试均已带证据完成。细节与验收以 EXECUTION_PLAN M 组为准。

Phase M 之后再议(不进入当前队列):偏好对话流标准化;组合风险最小集(组合波动率、最大回撤、HHI,仅此三个)。

### Phase H:交付硬化(仅当 NAS/HTTP/MCP 有真实稳定使用需求时)

HTTP 完整认证限速、MCP SDK 重写、health check 暴露降级状态、并发模型修复。

### 长期暂缓区

多 Agent 辩论、因子挖掘/因子库、回测框架、四层记忆系统、审计轨迹、相关性矩阵、美股第二行情源。

## 4. 禁止事项

- **禁止虚报完成:完成记录必须附可复现证据(grep 命中行号、测试名及断言),只写 commit hash 无效;勾选物理上未验证的验收项视为最严重违规。**
- 禁止实现暂缓区内容;禁止跳过任务顺序自行挑活;禁止重构与当前任务无关的代码。
- 禁止新增 .md 文件(未在决策日志登记理由前);分析/调研产物一律进 `docs/archive/`。
- 禁止变更 `AnalysisContext` schema 而不同步 `stocks/DATA_MODEL.md` + 本文档 + 测试。
- 禁止在缺数据时伪造指标;禁止把技术指标包装成投资建议。
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
- (追加格式:`日期:决定;依据`)
