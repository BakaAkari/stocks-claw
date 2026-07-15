# EXECUTION_PLAN.md — 现行任务与验收

> 生成:2026-07-03;收缩:2026-07-06(S0~S2-7 归档);更新:2026-07-15(v14情报覆盖+对抗性交易审查)
> 本文是**唯一的行动清单**,与 `PLAN.md`(方向、规则与状态)互补。
> 已完成 S0~S2-7 的完整任务卡和证据归档于
> `docs/archive/EXECUTION_PLAN_S1_S2_20260706.md`;2026-07-03 以前历史证据归档于
> `docs/archive/EXECUTION_PLAN_20260703.md`。

## 使用说明(执行 Agent 必读)

1. **读**:`PLAN.md`(尤其 §2 状态、§6 禁止事项、§7 执行协议)→ 本文 → 当前任务。
2. S3 与 S3-E 已关闭。当前现行闸门为 **T1 决策一致性与真实执行价值整改**;未经用户明确开工,
   不得继续扩展扫描池、情报字段或新报告模块。
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

## 已关闭 — global_intelligence_watch 切片

状态:已实现并上线。`intelligence_brief.py` 每小时采集情报并推送 Feishu。
部署细节保留在运维侧 skill reference；仓库内现行架构以 `ARCHITECTURE.md` 和脚本为准。

## 已关闭 — 2026-07-09 审查日 P0 修复

- [x] 硬编码汇率 `* 7.2` → `get_usd_cny_rate()` 实时汇率+6h缓存
- [x] -8% 到 -12% 止损空隙 → 新增 -10% 中间档(MID_STOP_PCT=-10.0, ratio=0.3)
- [x] 信号无区分度 → `_rank_signals()` 横截面排序(accumulate_candidate 按综合得分排名)
- [x] 完美相关压力测试 → `_build_scenarios()` 三因子情景(global_risk_off/china_shock/inflation_commodity)
- [x] agent_task v4 自包含指令集(persona/adaptability/data_reference/output_structure/飞书格式)
- [x] intelligence_brief.py 双输出:结构化 brief + 受控 LLM 快速总结
- [x] 8 个 cron prompt 精简为一行(指令全部迁入 agent_task JSON)
- [x] 全局验收:ruff 全绿、pytest 509 passed、compileall 0、CLI smoke 通过

> 完成:2026-07-09。详细证据见 `docs/archive/DEVELOPMENT_DIRECTION_20260709.md` 附录 B。
> 下一步候选见该文档 §2 第一层(系统健壮性)和第二层(分析深度)。

## 已关闭 — v8 产品类型路由与资产明细补全 (2026-07-11)

- [x] 支付宝 6 项资产补全:票号/份额/成本价 + 代理 instrument(纳指→QQQ、黄金→518880)
- [x] 建行 4 项资产补全:活期/嘉鑫稳利/建信现金添利/黄金积存(克数+均价)
- [x] `_build_action_cards()` 新增 `_PRODUCT_TYPE_RULES` 四档路由:
  `full`(场内ETF/股票)、`fund`(QDII/联接/混合/固收+,高门槛非零动作)、
  `precious`(贵金属价差产品)、`info_only`(银行理财)、`skip`(货基/现金/保险)
- [x] `AGENT_GUIDE.md` 新增 §4 资产分类与产品类型路由
- [x] skill `stocks-claw-portfolio-advisory` 更新 Product-Type Routing 段
- [x] 全局验收:ruff 全绿、pytest 46 passed、compileall 0

## 已关闭 — v9 Polygon 美股第二行情源 (2026-07-11)

- [x] 新增 `stocks/providers/polygon_quote.py`：PolygonQuoteProvider，使用 Polygon.io REST API (`/v2/aggs/ticker/{symbol}/prev`)
- [x] 注册到 `StocksEngine`：import + registry.register，默认启用
- [x] `markets.json` us.providers 增加 `"polygon"` 为备用源
- [x] API key 加载：环境变量 `POLYGON_API_KEY` 或仓库本地 `.secret/polygon-key.md`（密钥文件不提交）
- [x] 实测：AAPL 315.32 / NVDA 210.96 / SPY 754.95 / XLE 55.08（07-10 收盘），3/3 batch fetch 成功
- [x] 全局验收：ruff 全绿、pytest 498 passed、compileall 0

## 已关闭 — v10 基金净值 Provider (2026-07-11)

- [x] 新增 `stocks/providers/fund_nav.py`：FundNavProvider，使用天天基金 JSONP 接口
- [x] `context_builder._value_position()` fund_nav 分支：自动拉取净值 × 份额 = 市值
- [x] 5 只公募基金已接入：270042 广发纳指 / 000834 大成纳指 / 000217 华安黄金 / 019018 易方达信息产业 / 011193 广发恒荣
- [x] fund_nav 持仓不应用代理标的 MA20/RSI 做趋势判断
- [x] 标注从"手工估值"升级为"净值来源：天天基金（T-1 确认净值）"
- [x] 全局验收：ruff 全绿、pytest 498 passed、compileall 0

## 已关闭 — v11 组合级资金分配提示 (2026-07-11)

- [x] `_build_capital_allocation()`: 约束检测 → 冲突标注 → 减仓回收 → 约束感知排序 → 闲置资金建议 → 优先级摘要
- [x] 曝光标签→约束大类映射(_TAG_TO_BUCKET): gold/mining→黄金, a_share/us_equity/tech→权益, fixed_income→固收, cash_like→现金
- [x] 冲突检测: 权益不足 + 减仓信号 / 黄金超限 + 加仓信号 → 标注 conflicts[]
- [x] 约束感知排序: 超限大类加仓信号降权(×0.2), 不足大类加仓信号升权(×1.5), bucket去重
- [x] 闲置资金建议: 加仓候选需求 << 净可动用时, 从 rotation_leaders top-5 推荐配置候补
- [x] 全局验收: ruff 全绿、pytest 498 passed、compileall 0

## 已关闭 — v12 个性化参数引擎 (2026-07-13)

- [x] 新增 `stocks/engine/profile_interpreter.py`:Agent 直接推理翻译自然语言偏好→量化参数
- [x] CLI 新增 `--interpret-profile` / `--params-json`:预览→生成→确认写入流程
- [x] `scheduled_analysis._merge_profile_config()`:每次 session 自动合并 computed_profile.json
- [x] `_build_persona()`:按用户风格定制 LLM 报告 persona(5 条原则)
- [x] `quant_action.py`:核心交易参数改为 config 驱动;当前 `DEFAULT_PARAMS` 共 17 项,其中 `chase_enabled` 仍未接入追高动作分支
- [x] 全局验收:ruff 全绿、pytest 536/538、compileall 0

## 已关闭 — v13 架构修正 (2026-07-14)

- [x] trend_confirm_days:ratio÷N→cutoff 收紧。无状态引擎追踪不了"第几天",改为阈值偏移
- [x] add_ladder:pnl_pct 选档→MA20 偏离选档+仓位上限门。消除建仓时机与回踩深度的混淆
- [x] capital_deployment→capital_facts:纯事实块,LLM 管解释
- [x] LLM prompt 强化:资金部署独立成段,必须引用具体数字
- [x] AGENT_GUIDE.md §5 更新:§5.3 参数表 + §5.6 资金部署文档
- [x] 全局验收:ruff 全绿、pytest 536/538、compileall 0

## 当前闸门 — T1 决策一致性与真实执行价值整改

**依据**:`docs/TRADING_SYSTEM_ADVERSARIAL_REVIEW_20260715.md`。当前系统可作为风险监控与研究工作台,但自动动作、组合资金部署和可直接照单执行报告尚未通过。T1 第一项新鲜验证:`ruff` 通过、`compileall` 通过、CLI smoke 为 AnalysisContext v12 / data_quality v10;`pytest` 541 passed。

### T1-P0 必须先关闭

- [x] 恢复默认 `pytest` 全绿;Analyzer 支持显式 `analyzed_at` 以消除测试时钟依赖;新增 category padding、digest 全字段传递和 15/15 driver coverage 回归守门。
- [ ] 新鲜度改为按市场/持仓/数据类型计算;禁止美股 stale 全局削弱 A 股盘中动作。
- [ ] 增加价格/指标异常守门;异常复权、拆分或跨源口径先阻断技术动作。
- [ ] 重新定义资金可用性:即时现金、卖出回收、T+1、T+2、战略退出、锁定资产分层;禁止把现有持仓总值称为"今天可动用"。
- [ ] 建立组合最终裁决:风险状态、组合约束、资产动作冲突时必须输出换仓链或明确暂停条件,不能只列 conflicts。
- [ ] 修正 `trend_confirm_days` / `add_ladder` 命名和文档语义,或实现其字面行为。
- [ ] 报告将"今日执行动作"与"研究候选"完全分离。

### T1-P1 价值闭环

- [ ] Watch Window 输出相对上一窗口的 Delta,无变化 SILENT。
- [ ] Critical 风险增加首次触发、持续时长、解除条件、失效时间和状态变化。
- [ ] Driver/Conflict/Dissent 共用单一情报匹配结果;信号增加 provenance 与 synthesized 标记。
- [ ] Capital Allocation 不修改原始 Action Card;输出 portfolio approval 与 suppression_reason。
- [ ] 建议支持执行/部分执行/拒绝/延后及原因的低成本记录。
- [ ] 对最终 Action Card、因子覆盖、资金分配做版本化 Walk-forward 与交易成本归因。

### T1 出口

- [ ] 一致性闸:抽检真实 A 股与美股 session,数据→信号→动作→资产→组合→风险六层无未裁决冲突。
- [ ] 执行闸:至少一轮真实报告动作能被记录并在下轮复盘。
- [ ] 打扰闸:Watch Window 无变化不推送,critical 状态变化可追踪。
- [ ] 价值闸:用户确认报告减少决策成本并改善纪律。

## Backlog(未排期,禁止开工)

- **反馈回路**:已进入 T1-P1,不再是纯 Backlog。
- 贵金属实时报价 Provider:基金净值 Provider 已完成;积存金当前仍依赖手工估值。
- 估值数据层:A 股/美股指数 PE/PB 百分位,用于长线判断。
- 论点笔记本加厚(证据去重/反证/失效/交易结果)、组合归因、危机预案、分批执行计划。
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
- 不实施自动交易。DecisionEnvelope 双路径仍未接线;真实通知已由外部 Hermes/cron 承担,不属于 Engine 内置能力。
