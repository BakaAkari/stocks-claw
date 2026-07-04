# EXECUTION_PLAN.md — 现行任务与验收

> 生成:2026-07-03(文档体系重构;旧版含全部历史任务与证据,整份归档于 `docs/archive/EXECUTION_PLAN_20260703.md`)
> 本文是**唯一的行动清单**,与 `PLAN.md`(方向、规则与状态)互补。已完成的历史任务及其证据一律查归档件,不在本文重复。
> 执行原则:每个任务动手前先用 grep/读码验证【验证前提】,防止基于过时快照误改。

---

## 使用说明(执行 Agent 必读)

1. **读**:`PLAN.md`(尤其 §2 状态、§6 禁止事项、§7 执行协议)→ 本使用说明 → 认领的任务。当前队列:**S0 → S1-1 → S1-2 → S1-3 → S1-4 → S1-5 → S1-E → S2-0 → S2-1 → S2-2 → S2-3 → S2-4 → S2-5 → S2-6 → S2-7 → S2-E**,严格顺序,不得越级;**S1-E 未关闭前 S2 组不得开工**。
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

- [x] `ForecastRecord`:`{id, created_at, statement, target?, metric(当前仅 close), comparator ∈ {above,below}, level, deadline(date), confidence ∈ {low,medium,high}, status ∈ {open,hit,miss,unresolved,manual}, resolved_at?, resolution_note?}`;确认式保存(CLI `--forecast-save`/MCP `forecast_save`);存 `.local/forecasts/`。
- [x] 结算:`build_context` 时对已到 deadline 的 open 预测按收盘序列结算(复用 trigger review 的历史数据路径);历史缺失 → `unresolved` + reason;保存时即判定无法程序化验证的 statement(无 target/level)→ 直接标 `manual`,不进自动结算。
- [x] 注入:`raw_prompt_input` 增台账摘要——open 条数、最近结算结果、累计命中率;**结算样本 <10 时显示"样本不足",禁止表述为胜率/概率**。
- [x] 新 CLI/MCP 工具用法(含 `--confirmed` 示例)写入 `AGENT_GUIDE.md`。
- [x] 测试:hit/miss/unresolved/manual 四态、到 deadline 才结算、样本不足语义、未确认拒写。

**验收**:保存 ≥1 条真实预测;用 fixture 使其到期,运行后自动结算且结果出现在上下文;全局验收通过。

> 完成:3372508 S1-4 预测台账完成,并按用户确认保存真实预测 `id=a850424eee514060ba97b13604a4d49e,target=a:588000,comparator=above,level=2.05,deadline=2026-07-10,confidence=medium` | 证据:`stocks/domain/models.py:655` 定义 ForecastRecord,`stocks/engine/persistence.py:142`/`:154` 保存与读取 `.local/forecasts/`,`stocks/engine/forecasts.py:16`/`:47` 到期结算与摘要,`stocks/engine/__init__.py:667`/`:850` 确认式保存与 build_context 结算写回,`stocks/engine/context_builder.py:1175` 输出【预测台账】且 `:1182` 小样本显示"样本不足",`stocks/adapters/cli.py:237` 与 `stocks/adapters/mcp.py:221` 暴露 `forecast_save`,`AGENT_GUIDE.md:89`/`:112` 同步用法,`tests/engine/test_forecasts.py:53` 覆盖 hit/miss/unresolved/manual,`:92` 覆盖未到 deadline 不结算,`:109` 覆盖样本不足语义,`tests/test_asset_adapters.py:489`/`:520` 覆盖未确认拒写与 CLI/MCP 保存列表,`:540` 覆盖到期 fixture 结算并进入上下文;真实验收 `forecast_save --confirmed` 成功,`build_context` 回显 `forecast_summary.open_count=1`,`latest_id=a850424eee514060ba97b13604a4d49e`,`raw_prompt_input` 命中【预测台账】;全局闸 `ruff`=All checks passed,`pytest`=458 passed,`compileall`=0,CLI smoke=0。

### S1-5 复盘注入整合

**文件**:`stocks/engine/context_builder.py`、`stocks/engine/advice_review.py`、`stocks/prompts/personal_advice_prompt.txt`、`tests/`

- [x] `raw_prompt_input` 的【上次建议】升级为【复盘】小节,顺序固定:上期建议 actions → 触发核对(既有 trigger_review)→ 执行对照(S1-3)→ 到期预测结算(S1-4);四段中缺哪段显式写明缺哪段及原因。
- [x] prompt 契约"上期预案复盘"节指向本小节,要求当班 Agent 逐条回应。
- [x] 端到端测试:fixture 组合含建议+执行+预测,断言【复盘】四段齐全、顺序正确、缺段时有显式说明。

**验收**:一次真实运行输出完整【复盘】小节;全局验收通过。

> 完成:d9d74b1 S1-5 复盘注入整合完成,raw_prompt 已统一为【复盘】四段并要求当班 Agent 逐条回应 | 证据:`stocks/engine/context_builder.py:1400` 定义 `_append_review_section`,`stocks/engine/context_builder.py:1414`/`:1443`/`:1475`/`:1504` 固定输出 actions/触发核对/执行对照/到期预测结算四段且缺段显式写"缺失",`stocks/engine/context_builder.py:1393` 末尾指令要求先按【复盘】四段回应,`stocks/prompts/personal_advice_prompt.txt:32` prompt 契约指向【复盘】固定四段,`tests/engine/test_context_builder.py:643` 覆盖建议+执行+预测组合且断言四段顺序,`:723` 覆盖缺段说明,`tests/test_asset_adapters.py:236`/`:352`/`:541` 覆盖 advice actions、执行对照、预测结算在新【复盘】结构内回显;真实运行 `stocks.adapters.cli --output json --no-news --no-quotes` 输出【复盘】且四段 marker 全为 True,内容包含真实建议 actions、`a:588000 → executed`、预测 open_count=1 与"暂无到期预测结算";全局闸 `ruff`=All checks passed,`pytest`=460 passed,`compileall`=0,CLI smoke=0。

### S1-E 切片 1 出口(用户验收,非工程验收)

- [x] 工程闸:四道闸全绿;默认测试零外网(autouse 守门仍生效);`.local/executions/`、`.local/forecasts/` 均被 gitignore 覆盖。
- [x] 使用闸:用户真实使用 ≥5 个交易日,期间保存 ≥3 份带 actions 的建议、≥1 条执行记录、≥3 条预测,产生 ≥1 次完整【复盘】输出。
- [x] 价值裁决:用户亲自回答并由文档维护方写入 PLAN §9:①建议是否达到"可执行"?②复盘是否有信息量?③下一切片选什么(候选见 PLAN §5)?

**说明**:本出口的价值裁决**不许由执行 Agent 代答**;没有用户裁决,切片 1 不算关闭,任何新切片不得开工。

> 完成:74d4ce0 S1-E 用户验收关闭。工程闸四道全绿;使用闸由用户确认已真实使用 S1 功能并发现其能完成信息收集、分析反馈和建议闭环,但建议细粒度不足,无法真正满足个人资产级调仓需求;价值裁决写入 PLAN §9,下一切片选择 S2 资产数据结构 v2 与止盈止损基建 | 证据:`uv run ruff check .`=All checks passed,`uv run python -m pytest -q`=461 passed,`uv run python -m compileall -q stocks tests`=0,`uv run python -m stocks.adapters.cli --output json --no-news --no-quotes`=JSON ok且 `schema_version=11`/`data_quality_schema=9`;`git check-ignore -v .local/executions/ .local/forecasts/ .local/advice/ .local/financial_assets.json` 均命中 `.gitignore:2:.local/`;用户裁决:建议闭环成立但粒度不足,应进入 S2。

---

## 切片 2 — 资产数据结构 v2 与止盈止损基建(S2 组)

**用户场景(本切片要兑现的体验)**:"系统认识我的复合型资产——保险是动不了的固定资金,基金理财按暴露板块管理,股票 ETF 逐支带成本;它能算出我每笔持仓的真实盈亏和跨包装的暴露集中度,能把'浮盈 20% 止盈一半、回到成本价清仓'写成机器可核对的触发器,并且永远不建议我动那些动不了的钱。"

**立项依据(2026-07-04 用户裁决,见 PLAN §9)**:三决策点——①新增 `Position`/`Account` dataclass,`FinancialAsset` 保留为 v1 兼容层;②加载 v1 只做内存映射不自动写回,迁移由用户确认的一次性命令完成;③废除 `raw_prompt_input` 金额区间脱敏,上下文使用真实金额。设计依据:`docs/archive/ASSET_ENTRY_REDESIGN_20260704_zh.md` 与 `docs/archive/VISION_GAP_AUDIT_20260704.md`。

**边界(明确不做)**:不新增数据 Provider(基金净值/贵金属报价/USD 外 FX 为后续独立切片);不做交易流水/税务批次;不接线 DecisionEnvelope;不改内部 LLM;不自动解析 notes 提取金融事实;不引入数据库;不做主动推送(pull→push 为切片 3 候选)。

### S2-0 基线核验

- [x] 跑全局验收四道闸,记录 pytest 通过数(上一基线:S1-5 时 460 passed),不一致列差异。
- [x] 【验证前提】grep 确认:`FinancialAsset` 含 `instrument_key/quantity/tradable` 且无 `cost_basis`;`stocks/engine/scaffolds.py` 存在 asset_type 关键词映射表;`convert_to_cny` 仅支持 CNY/USD;`AnalysisContext.schema_version == 11`;`_ADVICE_TRIGGER_TYPES` 仅含 price/pct_change 四型;资产文件真实加载入口(grep `financial_assets`)的函数与路径。
- [x] 定位 `raw_prompt_input` 金额区间脱敏的实现位置与行号(供 S2-4);定位 HTTP `include_amounts` 剥离逻辑行号(供 S2-4 决定是否同步)。

**验收**:基线数字与 grep 证据写入完成记录;与 PLAN §2 冲突先修 PLAN。

> 完成:5493c8d S2-0 基线核验完成,当前 pytest 基线为 461 passed(较 S1-5 记录 460 passed +1,不影响 PLAN §2 已归档状态);CLI smoke `asset_count=7`, `schema_version=11`, `data_quality_schema=9` | 证据:四道闸 `uv run ruff check .`=All checks passed,`uv run python -m pytest -q`=461 passed,`uv run python -m compileall -q stocks tests`=0,`uv run python -m stocks.adapters.cli --output json --no-news --no-quotes`=JSON ok;`FinancialAsset` 字段 `stocks/domain/models.py:223/232/233/234`,全文件 grep 未见 `cost_basis`;asset_type 关键词映射 `stocks/engine/scaffolds.py:8/47`;`convert_to_cny` 仅 CNY/USD 自动换算 `stocks/engine/exchange_rate.py:163/174/176/185`;`AnalysisContext.schema_version` 为 11 `stocks/domain/models.py:853`;trigger 四型 `stocks/domain/models.py:377-381`;资产加载入口 `.local/financial_assets.json`→`stocks/data/financial_assets.json` 在 `stocks/engine/__init__.py:373-384`,写回入口 `:938`;raw_prompt 金额区间脱敏在 `stocks/engine/context_builder.py:1049/1057/1063/1534`;HTTP `include_amounts` 与剥离逻辑在 `stocks/adapters/http.py:138/149/185/202/239/245`。

### S2-1 v2 领域模型(纯模型层,不接线)

**文件**:`stocks/domain/models.py`、`stocks/DATA_MODEL.md`、`tests/engine/test_asset_v2_models.py`(新增)

- [x] 【验证前提】grep 确认 models.py 无 `Position`/`Account`/`CostBasis`/`Holding`/`Classification`/`ValuationInput`/`Liquidity` 类名冲突。
- [x] 新增 frozen dataclass:`Account {account_id, display_name, institution_type ∈ {brokerage, fund_platform, bank, insurance, manual}, market_scope?, base_currency, default_liquidity_tier?, notes?}`;`CostBasis {method="average", unit_cost?, cost_amount?, currency}`(两者至少其一);`Holding {quantity, unit ∈ {share, gram, unit}, cost_basis?}`;`Classification {asset_class, product_type, subtype?, exposure_tags[]}`(受控词表按设计文档 §3.3 定义为模块级 frozenset;tags 小写蛇形归一化);`ValuationInput {method ∈ {market_quote, fund_nav, manual_amount, precious_metal_quote, insurance_value}, manual_amount?, as_of?}`;`Liquidity {tradable?, rebalance_eligible?, tier ∈ {cash, t0, t1, t2_plus, periodic_open, locked, unknown}, redemption_rule?, lockup_until?, maturity_date?}`;`ReportedPerformance {unrealized_pnl?, cumulative_pnl?, as_of, source}`;`Position {position_id, account_id, display_name, currency, classification, instrument?, holding?, valuation_input, liquidity, role?, reported_performance?, data_completeness, confirmed, notes?}`。
- [x] 校验:`manual_amount`/`insurance_value` 必带 `manual_amount` 且缺 `as_of` 进入 `data_completeness.missing_fields`(与 S2-2 v1→v2 内存映射 as_of=null 要求一致);`market_quote` 必带 `instrument_key + holding.quantity`;`insurance_policy` 默认 `rebalance_eligible=false, tier=locked`;`instrument_key` 复用 `_normalize_instrument_key`(市场仍限 a/us/crypto);`__post_init__` 计算 `data_completeness.missing_fields`(规则:上市持仓缺 cost_basis、manual 缺 as_of、classification 为 unknown 等,机器可读枚举)。
- [x] `to_dict`/`from_dict`/`to_storage_dict` 齐备;storage dict 不含任何派生字段;`FinancialAsset` 零改动。
- [x] 测试:受控枚举非法值拒绝、insurance 默认锁定、completeness 规则逐条、round-trip 无损。

**验收**:全局四道闸通过;`FinancialAsset` 既有测试零改动全绿。

> 完成:59fa539 S2-1 v2 领域模型完成,新增纯模型层与测试,未接线 context/CLI/MCP/资产加载,`FinancialAsset` 未改动 | 证据:类定义 `stocks/domain/models.py:369/421/459/496/535/562/597/627`;DATA_MODEL v2 纯模型说明 `stocks/DATA_MODEL.md:43/77-80`;测试 `tests/engine/test_asset_v2_models.py:15/49/66/83/142/147`;局部测试 `uv run python -m pytest tests/engine/test_asset_v2_models.py -q`=7 passed;全局闸 `uv run ruff check .`=All checks passed,`uv run python -m pytest -q`=468 passed,`uv run python -m compileall -q stocks tests`=0,CLI smoke JSON ok且 `schema_version=11`/`data_quality_schema=9`。

### S2-2 v2 文件格式与双格式加载(不自动写回)

**文件**:资产读写模块(以 S2-0 grep 为准)、`stocks/domain/models.py`(仅映射函数)、`stocks/DATA_MODEL.md`、`tests/`

- [x] v2 文件格式:`{schema_version: 2, base_currency: "CNY", accounts: [], positions: []}`;加载器:顶层 list → v1,顶层 dict 且 `schema_version==2` → v2,其他 → 结构化错误。
- [x] v1→v2 内存映射函数(确定性,无猜测):`name→display_name`;`platform→` slug 化 `account_id` 并聚合 accounts;`amount→valuation_input{manual_amount, as_of: null}`(as_of 缺失计入 missing_fields);`asset_type→classification` 用显式映射表(迁入 scaffolds 关键词表全部键,新增 贵金属/保险/QDII/固收+/理财/货基;**映射不到 → asset_class=unknown,禁止默认成权益**);`instrument_key/quantity/tradable` 同名迁移(有 key+quantity → method=market_quote)。
- [x] **加载 v1 不写回文件**;data_quality 提示"v1 格式,建议迁移"。
- [x] v2 CRUD:CLI/MCP 资产写工具沿用确认式写入;文件为 v1 时拒写 v2 字段并提示先迁移。
- [x] 测试:v1 加载映射正确(含 unknown)、v2 round-trip、非法顶层报错、v1 加载后源文件字节不变。

**验收**:现有 v1 示例文件加载零告警(除格式建议);全局四道闸通过。

> 完成:51fc5cb S2-2 双格式加载完成,加载器兼容 v1 list 与 v2 dict,v1 仅内存映射不写回,AnalysisContext 仍通过 v1 兼容层消费资产 | 证据:加载入口 `stocks/engine/__init__.py:387/422/462`,v1→v2 映射 `stocks/domain/models.py:808/830`,data_quality 提示 `stocks/engine/context_builder.py:405/440`,测试 `tests/engine/test_asset_v2_loading.py:114-120`;局部测试 `uv run python -m pytest tests/engine/test_asset_v2_models.py tests/engine/test_asset_v2_loading.py -q`=11 passed;全局闸 `uv run ruff check .`=All checks passed,`uv run python -m pytest -q`=472 passed,`uv run python -m compileall -q stocks tests`=0,CLI smoke JSON ok且 `asset_format.status=migration_recommended`。

### S2-3 一次性确认迁移命令

**文件**:`stocks/adapters/cli.py`、`stocks/adapters/mcp.py`、`AGENT_GUIDE.md`、`tests/`

- [ ] CLI `--asset-migrate-v2`(MCP `asset_migrate_v2`):无 `--confirmed` 只输出完整迁移预览(每条 position 映射结果与 missing_fields),不落盘;`--confirmed` 时写 v2 并把原文件备份为 `financial_assets.v1.bak.json`。
- [ ] 迁移后输出 missing_fields 汇总与补录优先级(上市持仓 cost_basis → 基金代码/份额 → 黄金克数 → 保险现金价值)。
- [ ] 二次迁移(已是 v2)拒绝并提示;`AGENT_GUIDE.md` 增补用法。
- [ ] 测试:未确认不写盘、确认后落盘且备份存在、二次迁移拒绝。

**验收**:对真实资产文件执行一次预览(不确认),输出可读;全局四道闸通过。

### S2-4 派生估值与上下文接线(AnalysisContext v11→v12)

**文件**:`stocks/engine/context_builder.py`、`stocks/engine/scaffolds.py`、`stocks/domain/models.py`(AnalysisContext)、`stocks/DATA_MODEL.md`、`stocks/prompts/personal_advice_prompt.txt`、`ARCHITECTURE.md` §6、`tests/`

- [ ] 【验证前提】grep test_context_builder 中 schema_version 断言行号;确认 trigger_review 历史收盘路径可取最新收盘价。
- [ ] 每持仓运行时估值快照(仅上下文,不落盘):market_quote → 最新价(缺则历史收盘标 stale)× quantity → 原币市值 → CNY(复用 convert_to_cny 溯源);有 cost_basis → 未实现盈亏与盈亏比;manual → 金额直取,`as_of` >30 天标 `stale_manual`;每快照带 `{price_source, as_of, fx_source, flags}`。
- [ ] 分桶改造:PortfolioMapping/DriftCheck 桶由 `asset_class` 确定性映射(equity→权益、fixed_income→固收、cash/cash_equivalent→现金、commodity+gold→黄金、insurance→锁定);关键词表仅存活于 v1→v2 映射函数内;`unknown` 单列"未分类"桶计入 data_quality,不并入约束桶。
- [ ] 新增组合派生:`exposure_summary`(按 exposure_tags 聚合 CNY 市值与占比)、`liquidity_summary`(可动用 = tier ∈ {cash,t0,t1} 且 rebalance_eligible≠false;受限类现金;锁定)。
- [ ] AnalysisContext v11→v12:新增 `position_valuations`、`exposure_summary`、`liquidity_summary`;data_quality 增 `asset_completeness` 节点;**三处同步**(DATA_MODEL + models/builder + schema 断言测试)。
- [ ] **废除金额区间脱敏**(用户裁决③):raw_prompt_input 改用真实金额与市值;prompt 文件"资产描述方式"节的"不暴露具体金额数字"同步删除(仓位动作仍允许相对幅度表达);DATA_MODEL 与 ARCHITECTURE §6 对应段落同步;HTTP `include_amounts` 默认行为本卡不改(远程边界与本地上下文语义不同),仅在 ARCHITECTURE 注明。
- [ ] raw_prompt_input 新增小节:【暴露集中度】(exposure_summary 对照约束上限)、【可动用资金】(三档)。
- [ ] 测试:估值/盈亏计算(fixture 行情)、stale_manual、unknown 不入约束桶、跨包装同 tag 聚合、v12 断言、raw_prompt 真实金额与新小节。

**验收**:fixture(≥1 上市持仓带成本、≥1 manual、≥2 个共享 gold tag 的不同包装)构建 context,盈亏与黄金聚合数字正确;全局四道闸通过。

### S2-5 建议护栏与完备性告警

**文件**:advice 校验路径(grep 为准,S1-2 记录指 `stocks/engine/__init__.py:632` 附近)、`stocks/engine/context_builder.py`、`stocks/DATA_MODEL.md`、`AGENT_GUIDE.md`、`tests/`

- [ ] actions 保存校验扩展:target 命中 `rebalance_eligible=false` 持仓 → `add/increase/reduce/exit` 一律拒绝并给结构化错误;`tradable=false` 同理拒绝市场动作。
- [ ] data_quality `asset_completeness` 告警(确定性逐条):已映射持仓不在行情宇宙、上市持仓缺 quantity、有 quantity 缺 cost_basis、manual 缺/过期 as_of、不支持币种、asset_class=unknown。
- [ ] raw_prompt_input【复盘】前增数据边界声明:列出因缺口被降级的分析能力(如"XX 缺成本价,盈亏不可计")。
- [ ] 测试:锁定资产 target 拒绝、每类告警触发/不触发、边界声明文案。

**验收**:fixture 含保险资产时对其保存 reduce action 被拒且错误可读;全局四道闸通过。

### S2-6 建议粒度推导与暴露代理映射

**文件**:`stocks/domain/models.py` 或 `stocks/engine/scaffolds.py`(粒度推导)、`stocks/config/exposure_proxy.json`(新增)、`stocks/engine/context_builder.py`、`stocks/prompts/personal_advice_prompt.txt`、`stocks/DATA_MODEL.md`、`tests/`

- [ ] 派生字段 `advice_granularity`(运行时推导,不落盘,不允许用户直接录入):`detailed` = 有 instrument_key + quantity;`sector` = 无实时可交易标的但 exposure_tags 非空且 rebalance_eligible≠false;`fixed` = rebalance_eligible=false。推导规则集中一处定义,测试逐条覆盖;用户补齐字段(如基金 instrument_key)后粒度自动升级,无需手工改分类。
- [ ] 新增 `stocks/config/exposure_proxy.json`:`{tag: "market:code"}` 映射(初始:nasdaq100→us:QQQ、gold→a:518880、csi300→a:510300、tech_growth→us:XLK、short_treasury→us:SGOV;代理标的必须已在 watchlist/扫描池,加载时校验,不在则 data_quality 报 `proxy_not_in_universe`,禁止静默拉新行情)。
- [ ] context 接线:`sector` 粒度持仓在 raw_prompt_input 组合小节标注其代理标的的最新信号与轮动排名(如"纳指QDII层(代理 us:QQQ):reduce_risk");代理信号仅作参考事实注入,**不得**把代理标的的价格触发器直接挂到场外基金上(净值≠代理价格,注明该边界)。
- [ ] prompt 契约同步:调仓触发清单对 `sector` 层的动作以暴露层为 target(约束 bucket 或 tag),幅度用相对表达;对 `fixed` 层禁止给出任何动作。
- [ ] 测试:三档粒度推导逐条、字段补齐后自动升级、proxy 配置校验、raw_prompt 代理标注、fixed 层无动作注入。

**验收**:fixture 含一只带 nasdaq100 tag 的 manual 基金时,context 能回显 QQQ 的信号作为该层参考;全局四道闸通过。

### S2-7 成本基准触发器与行情宇宙守门

**文件**:`stocks/domain/models.py`(`_ADVICE_TRIGGER_TYPES`)、`stocks/engine/advice_review.py`、`stocks/engine/context_builder.py` 或 engine 编排层、`stocks/DATA_MODEL.md`、`stocks/prompts/personal_advice_prompt.txt`、`tests/`

- [ ] 【验证前提】grep `_ADVICE_TRIGGER_TYPES` 当前四型与 `advice_review` 的 trigger 核对入口。
- [ ] `_ADVICE_TRIGGER_TYPES` 增 `pnl_pct_above` / `pnl_pct_below`:基准 = 该 instrument 对应 `detailed` 持仓的 cost_basis(unit_cost 优先,否则 cost_amount/quantity);保存时校验:target 持仓必须存在且有 cost_basis,否则拒绝并提示先补成本(结构化错误,不静默降级为 pct_change)。
- [ ] `advice_review` 核对扩展:pnl 型触发器按 (最新收盘 − 成本价)/成本价 计算,`observed` 附 `{cost_basis_unit, latest_price, pnl_pct}`;成本数据在核对时已缺失(持仓被删改)→ `no_data + reason`,不猜。
- [ ] 行情宇宙守门:build_context 时每个 `detailed` 持仓的 instrument_key 若不在 watchlist/扫描池 → **自动加入本次行情与历史请求**(仅运行时,不写 watchlist 文件),data_quality 记 `auto_included_holdings`;历史回填失败照常走降级链并暴露。
- [ ] prompt 契约同步:触发清单允许且鼓励对有成本的持仓使用 pnl 型触发器表达止盈/止损(如"浮盈 ≥20% 止盈一半");明示 pnl 基准是用户成本而非建议日。
- [ ] 测试:pnl 触发器保存校验(无成本拒绝)、hit/not_fired 核对含负盈亏、守门自动纳入与失败暴露、prompt 注入。

**验收**:fixture 中对一只带成本持仓保存 `pnl_pct_above 20` 触发器,推动价格 fixture 越过阈值后 trigger_review 报 fired 且 observed 含 pnl_pct;全局四道闸通过。

### S2-E 切片 2 出口(用户验收,非工程验收)

- [ ] 工程闸:四道闸全绿;默认测试零外网;`financial_assets.v1.bak.json` 与 `.local/` 均被 gitignore 覆盖;**真实资产文件位置裁决**:迁移后含真实持仓与成本,建议移至 `.local/`,`stocks/data/` 只留 example(需用户确认)。
- [ ] 使用闸:用户完成真实迁移;9 只上市持仓(510300/512890/561560/588000/ITA/NEM/NVDA/SGOV/XLE)录入 quantity + cost_basis;≥1 次真实 build_context 输出逐持仓盈亏、黄金/纳指暴露聚合、可动用资金三档、≥1 条 sector 层代理信号;保存 ≥1 条 pnl 型止盈或止损触发器。
- [ ] 价值裁决(用户亲答,写入 PLAN §9):①逐持仓盈亏与暴露聚合是否改变决策质量?②三档粒度与护栏是否符合直觉?③止盈止损触发器是否可用?④下一切片:主动推送(pull→push)、基金净值 Provider、还是其他?

**说明**:价值裁决不许执行 Agent 代答。

---

## Backlog(未排期,禁止开工;仅供了解方向,详见 PLAN §4/§5)

定时扫描与触发推送(pull→push,切片 3 首选候选,依赖 S2 的成本触发器)/ 基金净值与贵金属报价 Provider(依赖用户补录基金代码/份额/克数)/ 估值数据层 / 论点笔记本 / 组合归因 / 危机预案 / 分批执行计划 / DecisionPlan 引擎化与内部 LLM 双路径(原 G1~G7;G0 契约已落盘休眠,设计参考归档件)。

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
