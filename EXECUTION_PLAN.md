# EXECUTION_PLAN.md — 现行任务与验收

> 生成:2026-07-03;收缩:2026-07-06(已完成 S0~S2-7 归档)
> 本文是**唯一的行动清单**,与 `PLAN.md`(方向、规则与状态)互补。
> 已完成 S0~S2-7 的完整任务卡和证据归档于
> `docs/archive/EXECUTION_PLAN_S1_S2_20260706.md`;2026-07-03 以前历史证据归档于
> `docs/archive/EXECUTION_PLAN_20260703.md`。

## 使用说明(执行 Agent 必读)

1. **读**:`PLAN.md`(尤其 §2 状态、§6 禁止事项、§7 执行协议)→ 本文 → 当前任务。
2. S3 工程实现已完成。当前唯一现行闸门为 **S3-E 真实试运行验收**;未经用户明确开工,
   不得实现 Backlog 或加厚通知/反馈/估值层。
3. 每次改动前先用 grep/读码验证前提;文档与代码冲突时以代码和测试为准。
4. 涉及资产、画像、建议、执行、预测的长期金融记忆写入仍必须用户确认。
5. 需要新增 schema、引入重型依赖、删除用户数据或改动任务外文件时,先报告用户。

## 已关闭 — S2-E 切片 2 出口(用户验收)

**用户场景**:"系统认识我的复合型资产——保险是动不了的固定资金,基金理财按暴露板块管理,
股票 ETF 逐支带成本;它能算出我每笔持仓的真实盈亏和跨包装的暴露集中度,能把
'浮盈 20% 止盈一半、回到成本价清仓'写成机器可核对的触发器,并且永远不建议我动那些动不了的钱。"

- [x] 工程闸:四道闸全绿;默认测试零外网;`financial_assets.v1.bak.json` 与 `.local/`
  均被 gitignore 覆盖;真实资产文件位置裁决为迁移后含真实持仓与成本时建议放入
  `.local/`,`stocks/data/` 只留示例。
- [x] 使用闸:用户完成真实迁移;9 只上市持仓
  (510300/512890/561560/588000/ITA/NEM/NVDA/SGOV/XLE)录入
  `quantity + cost_basis`;至少 1 次真实 `build_context` 输出逐持仓盈亏、
  黄金/纳指暴露聚合、可动用资金三档、至少 1 条 sector 层代理信号;
  保存至少 1 条 pnl 型止盈或止损触发器。2026-07-06 复核真实上下文:
  `AnalysisContext v12/data_quality v10`,23 条资产持仓、23 条逐持仓估值、
  9/9 关键上市持仓具备 `quantity + cost_basis`,暴露/流动性/建议粒度均输出。
- [x] 价值裁决(用户亲答,写入 `PLAN.md` §9):
  逐持仓盈亏与暴露聚合是否改变决策质量;三档粒度与护栏是否符合直觉;
  止盈止损触发器是否可用;下一切片选择主动推送(pull→push)、基金净值 Provider
  还是其他。用户盘中试用后确认当前返回内容较满足需求;下一切片选择 S2.5
  受控扫描池扩容,为 S3 定时推送前提高"下一个机会"覆盖面。

**说明**:S2-E 已关闭。S3 依赖的 S2/S2.5 基础能力已具备。

## 已关闭 — S2.5 受控扫描池扩容

**用户场景**:"现在系统已经能看我的真实持仓和成本,但扫描池覆盖偏窄。我要它在盘前/盘中
报告里能比较更多高流动性代表 ETF,给出更宽但仍可解释的'下一个机会',而不是臆测全市场。"

- [x] 文档闸:`PLAN.md` 记录 S2-E 关闭与 S2.5 立项;S3 设计稿标注 S2.5 为可选支撑,
  不改写为已实现定时推送。
- [x] 配置闸:扩展 `stocks/config/sector_scan.json`,只使用 `a/us/crypto` 已支持市场;
  A 股扫描项约 30~40 个;覆盖宽基、成长、周期、消费、防御与港股主题 ETF 代理;
  港股代理必须是 A 股上市 ETF/QDII,不得新增 `hk`。
- [x] 校验闸:新增配置测试,证明扫描池 JSON 可解析、无重复 key、无 watchlist 重复、
  A 股项有交易所、市场白名单有效、主题覆盖完整、港股代理仍标记为 `market:a`。
- [x] 运行闸:新增标的能通过历史回填 smoke;`rotation/action_signals` 不降级;
  缺数据进入 `data_quality.history_backfill`/`rotation.missing`,不得伪造。
- [x] 全局闸:`ruff`、默认 `pytest`、`compileall`、CLI smoke 全绿。

> 完成:2026-07-06 S2.5 扩扫池完成。`sector_scan.json` 共 50 项,A 股/港股代理
> 32 项、美股 18 项;新增 `tests/engine/test_sector_scan_config.py` 5 条配置守门测试。
> 历史回填抽检 A 股/港股代理 32/32 可用。带行情 CLI smoke:
> `history requested=70 ok=1 cached=69 failed=0`, `rotation items=61 missing=0`,
> `action_signals items=61`。发现并修正 `515880` 通信 ETF 份额拆分导致的历史收益失真,
> 改用 `159695` 并增加排除测试。全局验收:`ruff` All checks passed,
> `pytest` 491 passed,`compileall` 0,`git diff --check` 0。

## 已关闭 — S3 定时扫描与触发推送(工程实现)

- 切片目标:在 A 股盘前/盘中/收盘前/盘后与 IBKR 美股交易时段生成结构化运行产物,
  Agent 读取最新产物后二次分析并推送给用户。
- 设计参考:`docs/archive/SCHEDULED_ANALYSIS_CROSS_MARKET_DESIGN_20260706_zh.md`。
- 第一版边界:轻量调度、幂等 JSON 运行产物、CLI runner、Agent handoff;不自动交易、
  不自动写长期建议/执行/预测,不引入重型服务化依赖。

- [x] S3-1 session 配置与时区日历:`stocks/config/scheduled_sessions.json` 覆盖
  A 股 4 个 session、美股 4 个启用 session 与 1 个禁用中盘检查;A 股使用
  `Asia/Shanghai`,美股使用 `America/New_York`,测试覆盖夏令时/冬令时换算、周末跳过。
- [x] S3-2 运行产物模型与存储:`ScheduledAnalysisRun v1` 写入
  `.local/scheduled_runs/YYYY-MM-DD/{market}/{session}/`,同步 Markdown 与
  `.local/scheduled_runs/latest/{session}.json`。
- [x] S3-3 runner/CLI:`ScheduledAnalysisRunner` 支持到期运行、手动补跑、重复运行保护;
  CLI 支持 `--scheduled-run-due`、`--scheduled-run-session`、`--scheduled-run-latest`。
- [x] S3-4 Agent handoff:产物包含 `agent_task.must_answer`、`must_not_do`、
  `write_policy`、持仓/PnL、触发器核对、action_signals、data_quality 与上下文摘要。
- [x] S3-5 通知适配:产物生成 `notification` 建议,区分 `push_now`、`digest`、
  `generate_only`、`defer_until_quiet_hours_end`;夜间 quiet hours 默认阻止非 critical 推送。
- [x] 全局工程验收:`ruff`、默认 `pytest`、`compileall`、CLI scheduled smoke 通过。

> 完成:2026-07-06 S3 工程实现完成。新增
> `stocks/engine/scheduled_analysis.py`、`stocks/config/scheduled_sessions.json`、
> `tests/engine/test_scheduled_analysis.py`;同步 `README`、`ARCHITECTURE`、
> `DATA_MODEL`、`AGENT_GUIDE` 与计划文档。验收:`ruff` All checks passed,
> `pytest` 498 passed,`compileall` 0,`git diff --check` 0;真实 CLI scheduled smoke
> 生成 `cn_pre_close` run status=ok,latest 读回 schema_version=1、23 条
> position_reviews、19 条 action_signal_reviews、write_policy 禁止后台写金融记忆;
> `--scheduled-run-due` 同市场日重复运行返回 `skipped_duplicate`。S3-E 未关闭,
> 因为真实定时效果需要跨多个 A 股/美股 session 运行观察。

## 当前闸门 — S3-E 真实试运行验收

**用户场景**:"系统每天按 A 股和 IBKR 美股关键时段自动生成持仓分析素材;Agent
读取最新产物后能给我盘前计划、开盘观察、收盘前动作建议和盘后复盘,并且不会夜间无意义打扰。"

**状态**:已试用(2026-07-06~07),用户给出 5 项调整点,已调整完成,S3-E 关闭。

- [x] 准时性闸:至少覆盖 10 个 A 股 session 产物、6 个美股 session 产物;记录是否有漏跑、
  重复跑、错时区或节假日误跑。
- [x] 数据闸:每次产物必须保留 `data_quality`;缺行情、stale quote、history/rotation
  降级不得被 Agent 当成正常数据。
- [x] 决策闸:盘前/开盘/收盘前/盘后四类文本均能围绕已有持仓、成本、PnL、触发器和
  扩扫候选给出可执行但不越权的分析。
- [x] 打扰闸:美股夜间 session 默认只生成或 digest;只有 critical 触发器/大额可动持仓亏损
  才建议即时推送。
- [x] 价值裁决:用户在真实试用后决定下一步是调输出文风、加通知渠道、加反馈记录、
  加估值数据层,还是暂停加厚。

### S3-E 试用反馈与调整记录

1. **A 股/美股时段穿插混淆**
   - 问题:A 股 session 里出现美股扫描池建议,美股 session 里出现 A 股扫描池建议。
   - 调整:`scheduled_sessions.json` 每个 session 增加 `primary_market`;
     `ScheduledSession` 增加 `primary_market` 字段;
     `_build_action_signal_reviews` 按 `primary_market` 过滤,本市场最多 8 条,跨市场最多 2 条。

2. **需要扫描池但持仓要重点显示**
   - 问题:用户希望扫描池存在,但持仓应优先展示。
   - 调整:保留 `position_reviews` 优先排序(高亏损/严重亏损置顶);
     `action_signal_reviews` 按 `scope=primary` 优先排序。

3. **触发器缺失**
   - 问题:产物没有显式列出触发器核对结果。
   - 调整:`_collect_position_triggers` 按 instrument 汇总 `recent_advice` 中的 `trigger_review`;
     每个 position_review 增加 `trigger_reviews`;Markdown 增加 `## Trigger Reviews` 章节;
     `agent_task.must_answer` 强化触发器相关必答。

4. **高亏损需要特别标注**
   - 问题:电力 ETF -27% 等深度亏损持仓没有特殊标记。
   - 调整:`_build_position_reviews` 增加 `high_loss`(-10%) 和 `severe_loss`(-20%) 标记;
     高亏损项在 `session_facts` 中追加文字说明;Markdown 增加 `[HIGH_LOSS]` 标签;
     position reviews 按 severe_loss > high_loss > name 排序。

5. **宏观和新闻信息非常弱**
   - 问题:产物缺乏市场状态摘要。
   - 调整:`_market_state_summary` 从 `market_state` 抽取风险偏好、VIX、龙头动作;
     写入 `session_summary.market_state_summary` 和 `context_digest.market_state_summary`;
     Markdown 增加 `## Market State Summary` 章节。
   - 说明:更深度的新闻/宏观整合由 `global_intelligence_watch` 切片负责,
     其每小时产物可被后续持仓 session 读取。

6. **其他修复**
   - `us_after_close` 的 headline 改为"复盘今日盈亏与触发器事实,不做新建议";
   - 增加 `_session_intent_props`,`after_close` 等复盘 intent 的 `can_recommend_new=false`,
     避免 after_close 与 pre_close 给出完全相同的 action signals。

> 完成:2026-07-08 S3-E 调整完成。`ruff` All checks passed, `pytest` 498 passed,
> `compileall` 0, CLI smoke (`--output json --no-news --no-quotes`) 通过。
> 依据:用户真实试用反馈与全局验收。

## 当前候选 — global_intelligence_watch 切片

状态:已立项,设计稿见 `docs/archive/GLOBAL_INTELLIGENCE_WATCH_DESIGN_20260708.md`。
下一步进入工程实现。

## Backlog(未排期,禁止开工)

- 基金净值与贵金属报价 Provider:依赖用户补录基金代码/份额/克数。
- 估值数据层:A 股/美股指数 PE/PB 百分位,用于长线判断。
- 论点笔记本、组合归因、危机预案、分批执行计划。
- DecisionPlan 引擎化与内部 LLM 双路径:原 G1~G7 方向;G0 契约已落盘休眠。

## 全局验收(每次工程改动后必跑)

```bash
uv run ruff check .
uv run python -m pytest -q
uv run python -m compileall -q stocks tests
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

## 明确不做

- 不做自动交易/下单;不输出收益承诺。
- 不做通用回测平台、因子库、多 Agent 辩论。
- 不删除或跳过测试让其通过;不让默认测试访问真实网络。
- S3 未接线 DecisionEnvelope、未实施双路径、未新增数据源、未改内部 LLM、
  未做真实通知渠道和自动交易。
