# EXECUTION_PLAN.md — 现行任务与验收

> 生成:2026-07-03(文档体系重构;旧版含全部历史任务与证据,整份归档于 `docs/archive/EXECUTION_PLAN_20260703.md`)
> 本文是**唯一的行动清单**,与 `PLAN.md`(方向、规则与状态)互补。已完成的历史任务及其证据一律查归档件,不在本文重复。
> 执行原则:每个任务动手前先用 grep/读码验证【验证前提】,防止基于过时快照误改。

---

## 使用说明(执行 Agent 必读)

1. **读**:`PLAN.md`(尤其 §2 状态、§6 禁止事项、§7 执行协议)→ 本使用说明 → 认领的任务。当前队列:**S0 → S1-1 → S1-2 → S1-3 → S1-4 → S1-5 → S1-E**,严格顺序,不得越级。
2. 每完成一个任务,跑全局验收(文末),全部通过后才能进入下一任务。
3. 勾选格式:完成后把 `- [ ]` 改为 `- [x]`,任务末尾追加一行 `> 完成:<commit> <一句话说明> | 证据:<grep 命中行号/测试名及关键断言>`。**只写 commit hash 无效;勾选未验证的项是最严重违规。**
4. 前提不成立(代码已改过、文件不存在等)→ 记 `> 跳过:<原因>`,不改代码,报告用户。
5. 需要动任务清单外的文件、想到"更好的方案"、涉及删用户数据/schema 变更/新增依赖 → **停止并报告**。
6. 修改 PLAN/本文前必须读取当前版本逐行比对,只做增量修改,禁止整体覆盖;改完立即 `git add + commit`。

---

## S0 — 基线核验(开工第一件事)

**目的**:PLAN §2 的状态是 2026-07-03 文档重构时从归档件转录的,未重新逐项核验。本任务为后续一切工作建立可信基线。

- [x] 跑全局验收四道闸,记录 pytest 收集数与通过数;与 PLAN §2 转录值("最近一次全量 431 passed")对照,不一致则逐项列出差异。
- [x] 【验证前提】grep 确认:`stocks/engine/advice_review.py` 存在 `_review_trigger`;`stocks/engine/history_provider.py` 存在 Tencent/Nasdaq/Binance K 线 Provider,`stocks/providers/filings.py` 存在 SEC/巨潮 filing Provider;G0 的 DecisionEnvelope 契约与本地校验器存在(休眠资产,不得删除);`AnalysisContext.schema_version == 10`、`data_quality.schema_version == 9`(以 `stocks/DATA_MODEL.md` 与 models/测试断言互证)。
- [x] 真实网络冒烟一次完整 `build_context`(含行情与新闻),把 `data_quality` 概要(quotes/history_backfill/rotation/upcoming_events 各节状态)留存到本任务完成记录。

**验收**:基线数字写入完成记录;若与 PLAN §2 冲突,先修正 PLAN §2(增量修改+立即 commit)再继续。

> 完成:7ac9ee4 S0 基线核验通过,PLAN §2 当前基线由 431 更新为 436 passed,并修正 K 线 Provider 真实路径 | 证据:全局闸 `uv run ruff check .`=All checks passed,`uv run python -m pytest -q`=436 passed,`uv run python -m compileall -q stocks tests`=0,`uv run python -m stocks.adapters.cli --output json --no-news --no-quotes`=0;grep `stocks/engine/advice_review.py:66`,`stocks/engine/history_provider.py:145/288/382`,`stocks/providers/filings.py:18/146`,`stocks/engine/decision_contract.py:18/49`,`stocks/domain/models.py:499/530`,`stocks/engine/context_builder.py:256/391`,`stocks/DATA_MODEL.md:4/235`,`tests/engine/test_context_builder.py:146-153`;真实网络 build_context:quotes ok 11/11,history_backfill ok 37 skipped_cached,rotation ok missing 0,upcoming_events ok cache hits 3 misses 0,news partial(SEC_USER_AGENT 未配置邮箱)。

---

## 切片 1 — 建议闭环(S1 组)

**用户场景(本切片要兑现的体验)**:"我在对话里得到一份落到我持仓上的、带幅度和触发条件的建议;我说'照做了'或'没做';一周后它拿着收盘事实找我复盘,并告诉我它此前的预测中了几条。"

**边界**:不建决策引擎、不实施双路径、不新增数据源、不做内部 LLM 改造。一切结构落在既有的 AdviceRecord/persistence/context_builder 路径上。G0 已冻结的 DecisionEnvelope/DecisionPlan 契约在本切片**不接线**,但 S1-2 的 actions 字段命名应尽量与 DecisionPlan 对应字段对齐,便于未来映射;若发现无法对齐,停下报告而不是自造第三套语义。

### S1-1 最小持仓映射

**文件**:`stocks/domain/models.py`、资产 CRUD 与持久化(`stocks/adapters/cli.py`、`stocks/adapters/mcp.py`、engine 资产读写)、`stocks/engine/context_builder.py` 或 `scaffolds.py`、`stocks/DATA_MODEL.md`、`tests/`

- [x] 【验证前提】grep 确认 `FinancialAsset` 当前没有 `instrument_key`/`quantity`/`tradable` 字段;确认资产写路径(CLI/MCP)均要求 confirmed。
- [x] `FinancialAsset` 增可选字段:`instrument_key`(格式 `market:code`,非证券资产为 None)、`quantity`(可 None)、`tradable`(可 None);旧记录缺字段照常加载,round-trip 不丢失、不改写源金额/币种。
- [x] 映射只能来自用户确认的写操作;**禁止按资产名称模糊猜证券代码**;`--asset-update`/MCP `asset_update` 支持这三个字段,格式非法(不匹配 `market:code` 或市场未知)拒绝并给结构化错误。
- [x] context 侧:已映射资产与 watchlist/行情按 `instrument_key` 关联;`raw_prompt_input` 组合小节对已映射持仓标注"当前持有"(有 quantity 则带上);未映射资产照旧参与资产桶,不阻塞。若 `AnalysisContext` 字段有变化,schema 单调 +1 并三处同步。
- [x] 测试:旧记录兼容加载、未确认拒写、round-trip、非法 instrument_key 拒绝、映射后 context 标注正确。

**验收**:用户真实资产中 ≥1 条证券持仓完成映射后,`raw_prompt_input` 能看到该标的"当前持有";全局验收通过。

> 完成:b12ad1d S1-1 最小持仓映射完成,并按用户确认将真实资产"科创50ETF华夏"映射为 `a:588000`/`quantity=1800`/`tradable=true` | 证据:`stocks/domain/models.py:221-303` 定义/校验/持久化字段,`stocks/adapters/cli.py:240-258` 与 `stocks/adapters/mcp.py:105-141` 支持确认式写入,`stocks/engine/context_builder.py:1032-1053`/`:1187-1196` 标注已映射持仓,`tests/engine/test_engine.py:293` 覆盖旧记录兼容与 round-trip,`tests/test_asset_adapters.py:100` 覆盖 CLI/MCP 校验,`tests/engine/test_context_builder.py:611` 覆盖 raw_prompt "当前持有";真实验收命中 `"instrument_key":"a:588000","quantity":1800.0,"tradable":true` 与 raw_prompt `科创50ETF华夏 ... 标的: a:588000 | 当前持有 1800 | 可交易`;全局闸 `ruff`=All checks passed,`pytest`=439 passed,`compileall`=0,CLI smoke=0。

### S1-2 结构化建议 actions(AdviceRecord 扩展,不建引擎)

**文件**:`stocks/domain/models.py`、`stocks/engine/persistence.py`、advice_save 路径(cli/mcp)、`stocks/prompts/personal_advice_prompt.txt`、`stocks/DATA_MODEL.md`、`tests/`

- [x] 【验证前提】grep AdviceRecord 现有字段、triggers 的校验方式与存储位置(`.local/advice/`)。
- [x] `AdviceRecord` 增可选 `actions[]`,每项:`{target(instrument_key 或约束 bucket 名), action ∈ {add,increase,reduce,exit,hold,watch}, size_hint(比例区间或自然语言,禁止精确金额), trigger?, invalidation?, horizon ∈ {short,medium,long}}`;旧记录按 `[]` 兼容加载。
- [x] 保存时确定性校验:`target` 必须存在于已映射持仓/watchlist/扫描池/约束 bucket 之一;action/horizon 枚举合法;`size_hint` 含具体货币金额(如 `¥12,000`、`$3,400`、`12000元`,正则检测货币符号/单位+数字)则拒绝,百分比与比例区间(如 "一成"、"5%~8%")允许。校验失败返回结构化错误明细,不静默丢弃、不部分写入。
- [x] prompt 契约"调仓触发清单"节同步一句:建议确认保存时按 actions 结构落库;`AGENT_GUIDE.md` 的 advice_save 示例同步 actions 用法。
- [x] 测试:合法保存、伪造 target 拒绝、精确金额拒绝、旧记录加载、CLI/MCP 透传。

**验收**:保存一条带 ≥2 个 actions 的真实建议,下次 `build_context` 的 recent_advice 完整回显 actions;全局验收通过。

> 完成:773b8a9 S1-2 结构化建议 actions 完成,并按用户确认保存真实建议两条 actions(`a:588000 increase 5%~8%`,`现金 reduce 一成以内`) | 证据:`stocks/domain/models.py:384`/`:402`/`:504` 定义 actions 与精确金额拒绝,`stocks/engine/__init__.py:78`/`:632` 返回结构化校验错误并校验 target,`stocks/engine/context_builder.py:1096` 将 actions 注入 raw_prompt,`stocks/prompts/personal_advice_prompt.txt:53` 与 `AGENT_GUIDE.md:95` 同步契约,`tests/engine/test_advice_actions.py:32` 覆盖旧记录 actions=[],`tests/test_asset_adapters.py:234`/`:316` 覆盖 MCP/CLI 透传、伪造 target 与精确金额拒绝;真实验收 `advice_save --confirmed` 成功,`build_context` 回显 recent_advice.actions 两条且 raw_prompt 命中 `结构化动作:`、`a:588000 | increase | 5%~8% | short`、`现金 | reduce | 一成以内 | short`;全局闸 `ruff`=All checks passed,`pytest`=446 passed,`compileall`=0,CLI smoke=0。

### S1-3 执行记录

**文件**:`stocks/domain/models.py`、`stocks/engine/persistence.py`、`stocks/adapters/cli.py`、`stocks/adapters/mcp.py`、`stocks/DATA_MODEL.md`、`tests/`

- [x] `ExecutionRecord`:`{id, advice_id?, target, action(建议 action 枚举 + none 表示"明确未执行"), extent ∈ {full,partial}(action=none 时省略), note, executed_at, recorded_at}`;存 `.local/executions/`(gitignore 确认覆盖);确认式写入:CLI `--execution-save ... --confirmed`、MCP `execution_save`(`confirmed: true`);`--execution-list`/`execution_list` 读取。
- [x] `build_context` 复盘对照:上期建议每条 action 按 advice_id+target 匹配——有记录且 extent=full → `executed`;extent=partial → `partial`;action=none → `not_executed`;无记录 → `unknown`。**匹配不到一律 unknown,不猜。**
- [x] 新 CLI/MCP 工具用法(含 `--confirmed` 示例)写入 `AGENT_GUIDE.md`。
- [x] 测试:未确认拒写、关联/不关联建议两种保存、对照四态、round-trip。

**验收**:记录一条真实执行后,下次上下文出现"建议 vs 执行"对照;全局验收通过。

> 完成:b75427e S1-3 执行记录完成,并按用户确认保存真实执行记录 `advice_id=2026-07-03T04:40:29.112458+00:00,target=a:588000,action=increase,extent=full` | 证据:`stocks/domain/models.py:542` 定义 ExecutionRecord,`stocks/engine/persistence.py:115`/`:127` 保存与读取 `.local/executions/`(根 `.gitignore:2` 覆盖 `.local/`),`stocks/adapters/cli.py:183`/`:215` 与 `stocks/adapters/mcp.py:204` 暴露确认式工具,`stocks/engine/advice_review.py:66` 按 advice_id+target 精确匹配,`stocks/engine/context_builder.py:1114` 输出"建议 vs 执行",`AGENT_GUIDE.md:103` 与 `stocks/DATA_MODEL.md:89` 同步文档,`tests/engine/test_persistence.py:84` 覆盖 round-trip,`tests/test_asset_adapters.py:349`/`:430` 覆盖未确认拒写、关联/无关联保存、四态对照与 CLI 列表;真实验收 `execution_save --confirmed` 成功,`build_context` 回显 `a:588000 → executed | 记录 increase/full`,未记录的 `现金 → unknown`;全局闸 `ruff`=All checks passed,`pytest`=450 passed,`compileall`=0,CLI smoke=0。

### S1-4 预测台账

**文件**:`stocks/domain/models.py`、`stocks/engine/forecasts.py`(新增)、`stocks/engine/persistence.py`、cli/mcp、`stocks/engine/context_builder.py`、`stocks/DATA_MODEL.md`、`tests/`

- [ ] `ForecastRecord`:`{id, created_at, statement, target?, metric(当前仅 close), comparator ∈ {above,below}, level, deadline(date), confidence ∈ {low,medium,high}, status ∈ {open,hit,miss,unresolved,manual}, resolved_at?, resolution_note?}`;确认式保存(CLI `--forecast-save`/MCP `forecast_save`);存 `.local/forecasts/`。
- [ ] 结算:`build_context` 时对已到 deadline 的 open 预测按收盘序列结算(复用 trigger review 的历史数据路径);历史缺失 → `unresolved` + reason;保存时即判定无法程序化验证的 statement(无 target/level)→ 直接标 `manual`,不进自动结算。
- [ ] 注入:`raw_prompt_input` 增台账摘要——open 条数、最近结算结果、累计命中率;**结算样本 <10 时显示"样本不足",禁止表述为胜率/概率**。
- [ ] 新 CLI/MCP 工具用法(含 `--confirmed` 示例)写入 `AGENT_GUIDE.md`。
- [ ] 测试:hit/miss/unresolved/manual 四态、到 deadline 才结算、样本不足语义、未确认拒写。

**验收**:保存 ≥1 条真实预测;用 fixture 使其到期,运行后自动结算且结果出现在上下文;全局验收通过。

### S1-5 复盘注入整合

**文件**:`stocks/engine/context_builder.py`、`stocks/engine/advice_review.py`、`stocks/prompts/personal_advice_prompt.txt`、`tests/`

- [ ] `raw_prompt_input` 的【上次建议】升级为【复盘】小节,顺序固定:上期建议 actions → 触发核对(既有 trigger_review)→ 执行对照(S1-3)→ 到期预测结算(S1-4);四段中缺哪段显式写明缺哪段及原因。
- [ ] prompt 契约"上期预案复盘"节指向本小节,要求当班 Agent 逐条回应。
- [ ] 端到端测试:fixture 组合含建议+执行+预测,断言【复盘】四段齐全、顺序正确、缺段时有显式说明。

**验收**:一次真实运行输出完整【复盘】小节;全局验收通过。

### S1-E 切片 1 出口(用户验收,非工程验收)

- [ ] 工程闸:四道闸全绿;默认测试零外网(autouse 守门仍生效);`.local/executions/`、`.local/forecasts/` 均被 gitignore 覆盖。
- [ ] 使用闸:用户真实使用 ≥5 个交易日,期间保存 ≥3 份带 actions 的建议、≥1 条执行记录、≥3 条预测,产生 ≥1 次完整【复盘】输出。
- [ ] 价值裁决:用户亲自回答并由文档维护方写入 PLAN §9:①建议是否达到"可执行"?②复盘是否有信息量?③下一切片选什么(候选见 PLAN §5)?

**说明**:本出口的价值裁决**不许由执行 Agent 代答**;没有用户裁决,切片 1 不算关闭,任何新切片不得开工。

---

## Backlog(未排期,禁止开工;仅供了解方向,详见 PLAN §4/§5)

估值数据层 / 定时扫描与触发推送 / 论点笔记本 / 组合归因 / 危机预案 / 分批执行计划 / DecisionPlan 引擎化与内部 LLM 双路径(原 G1~G7;G0 契约已落盘休眠,设计参考归档件)。

---

## 全局验收(每个任务完成后必跑)

```bash
uv run ruff check .
uv run python -m pytest -q
uv run python -m compileall -q stocks tests
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

## 明确不做(防止执行 Agent 跑偏)

- 不做自动交易/下单;不输出收益承诺。
- 不做通用回测平台、因子库、多 Agent 辩论。
- 不删除或跳过测试让其通过;不让默认测试访问真实网络。
- 本切片不接线 DecisionEnvelope、不实施双路径、不新增数据源、不改内部 LLM(G0 契约休眠保留,后续见归档件与 PLAN §5,待切片 1 用户裁决后再议)。
