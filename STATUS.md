# STATUS.md

The **only** source of current dynamic project state. `ROADMAP.md`,
`ARCHITECTURE.md`, and `stocks/DATA_MODEL.md` describe direction and shape;
none of them record phase/completion status — that lives here only.

> Update this file once per task, right after that task's implementation is
> stable and its focused tests pass. Overwrite the stale sections below;
> don't append a history log here (decision history belongs in `PLAN.md`).
>
> Last updated: 2026-08-06 (fourteenth update) — **第五轮对抗性校验
> （R5-2/4/5/7/8/10：freshness 交易日历日语义 / 资产合计 / 生成时间 /
> 高风险持续提示 / 估值 key 修正 / §3§5 去重）**。此前：2026-08-06
> （thirteenth）第四轮对抗性校验 P5-1..P5-7；2026-08-06（twelfth）
> C1 报告决策支持层补全；2026-08-06（eleventh）四轮全量修复。
> Next: 双引擎信息面专项 / 用户中立化清理 / W1（按需求分析 §7 排序）。

## 2026-08-06 第五轮对抗性校验(R5-2/4/5/7/8/10)

**Full pytest 1386 passed, 7 skipped, 1 deselected（仅排除既有基线失败
`test_advice_feedback::TestLedgerWrite::test_engine_rollup_reads_ledger`）；
ruff/compileall clean。方法：NAS 生产环境用最新代码（a9a828f）重新
触发完整报告（8/6 21:08 cn_after_close），从普通用户/设计师/交易分析
师三视角审查，逐项回源码验证后一次性修复。**

## 2026-08-06 第四轮对抗性校验(P5-1..P5-7)

**Full pytest 1386 passed, 7 skipped, 1 deselected（仅排除既有基线失败
`test_advice_feedback::TestLedgerWrite::test_engine_rollup_reads_ledger`）；
ruff/compileall clean。方法：用当前修复后代码重新触发真实报告
（8/6 19:49 cn_after_close），从普通用户/设计师/交易分析师三视角
审查输出与源码底层，验证修复效果。**

## 2026-08-06 C1 报告决策支持层补全

**Full pytest 1380 passed, 7 skipped, 1 deselected（仅排除既有基线失败
`test_advice_feedback::TestLedgerWrite::test_engine_rollup_reads_ledger`）；
ruff/compileall clean；新增 12 个回归测试（test_report_defects_p234.py
现共 19 个）。**

### C1-WP1 — 冲突确定性解读

`presentation.py` 新增 `_conflict_tilt`(不依赖 LLM 的确定性规则):
- stop_loss → `action`(硬止损不受约束限制,冲突仅为复核提示)
- reduce/take_profit 且 bucket 低于下限 → `constraint`(低配区再降加深偏离)
- 加仓类且 bucket 高于上限 → `constraint`(高配区不宜再加)
- 其余 → `manual`(需人工裁定)

`_conflict_detail` 增加 `tilt` + `tilt_reason` 字段,交易员可直接消费
"倾向维持/倾向执行/需人工"结论。8/6 实测场景(科创50 减仓 vs 权益低配
12.7%<25%)正确输出 constraint + "低于下限" 理由。

### C1-WP2 — 研判边界自动降级

`advisory_mainline.py` 新增 `_apply_freshness_downgrade`,在
`build_advisory_outlook` 定稿前确定性降级:
- macro 官方统计或任一主市场行情 freshness=old/stale → 置信度降一级
  (high→medium→low,low 不再降)
- `data_limitations` 追加"研判基于 N 天前宏观数据,可信度已自动降级"
- **不改 rationale/validation 文本**——诚实保留 LLM 原话,只调可信度

8/6 实测:宏观 6/1 数据 + 研判 medium 置信 → 自动降为 low。

### C1-WP3 — 确定性明日计划

`presentation.py` 新增 `_tomorrow_plan`(非 LLM 创作,输入可追溯):
- approved_actions → 高优先级执行项(带比例提示)
- conflict tilt → 维持/需裁定项
- data_notes → 资金/数据核对项
- risk_state → 风险档位纪律提示
- outlook 低可信 → "明日以人工盯盘为准"
- 无操作 → 输出观察项

`build_push_payload.py` §6 后渲染"明日计划"节(①/②/③ 优先级标记)。
用户面不暴露内部 position_id(公开 code/名称)。

### 验证

- 全量 1380 passed / 7 skipped / 1 deselected;新增 12 个回归测试
  (冲突 tilt 4 + 降级 4 + 明日计划 4);
- ruff/compileall clean;
- 已 commit + push + NAS 同步(见 git log C1 相关 commit)。

### R5 修复明细(本轮)

- **R5-2(核心) freshness 交易日历日语义**(`context_builder.py`
  `_freshness_from_datetime` + `_MARKET_TZ`):旧逻辑按纯时间差
  (2h fresh / 24h stale)判 stale,盘后报告把当日收盘价(如 A股 08:14
  行情在 19:58 生成)误判为过时 → 研判全拒、动作全暂缓。改为市场本地
  时区日历日判定(同日 fresh / 昨日 stale / 更早 old),新增 `_MARKET_TZ`
  映射(a/cn→Asia/Shanghai、us→America/New_York、crypto→UTC);全局
  freshness 用 UTC 保守聚合。效果:盘后报告恢复完整研判与执行判定。
- **R5-4 资产合计**(`presentation.py` `_cash_view` + §6 渲染):
  资金行后新增"资产合计 ¥1,527,720(各资金桶加总,含安全垫与待决)",
  交易分析师可核对加总。数字来自 `cash.total_assets_cny`(与各桶同源,
  validator 已授权)。
- **R5-5 生成时间**(`build_push_payload.py`):标题标注
  "*生成时间 2026-08-06 21:08*",用户判断报告时效;完整 ISO 格式避开
  number gate;payload 新增 `generated_at`(2 个既有测试同步更新)。
- **R5-7 高风险持续提示**(`build_push_payload.py` §1):transition=
  unchanged 但 suspend_accumulation(仍 hedge/reduce)时,窗口变化显示
  "风险状态持续: 对冲/高风险",不再误报"本窗口未发现新证据"。
- **R5-8 估值 key 修正**(`context_builder.py` 901 行):message 用
  `valuation_age_days`/`as_of`(此前读不存在的 `_valuation_age_days`
  产生"估值为 None 天前(截止 )"垃圾文本);None 回退"估值时效待确认"。
- **R5-10 §3/§5 去重**(`build_push_payload.py` §5):manual_review 时
  已展示的 no_action_reasons 通过 already_shown + 前缀匹配在
  why/do_not_do 中跳过,消除广发纳指 §3/§5 双写。
- **R5-1 撤销**:顶层 quotes.as_of 仅元数据,用户面 by_market 渲染
  正确(A股截止 08:14),不构成误读。

验证:全量 1386 passed / 7 skipped / 1 deselected;ruff/compileall
clean。NAS 重放 8/6 21:08 cn_after_close——A股行情判 fresh 后研判
恢复生成(置信度自动降级"置信 低")、资产合计显示、估值提示正常
("余额宝 估值为 26 天前(截止 2026-07-11)")、风险触发原因全中文、
明日计划 7 条完整。更新 4 个既有测试断言为新语义。

- **P5-1 明日计划 gate 对齐**(`presentation.py` `_tomorrow_plan`):
  `_tomorrow_plan` 此前直接消费 `approved_actions`,把行情过时被 gate
  的动作仍列为 high 执行,与指令卡"暂缓执行"矛盾。修复:传入
  `by_market`,用与指令卡相同的 `_is_executable` gate——可执行 → high,
  被 gate → medium 复核(文案即"暂缓执行,等待数据恢复")。
- **P5-2 估值过期聚合**(`build_push_payload.py` §5):14 条"手工估值超
  30 天"提示被 `collected[:8]`+`ordered[:4]` 截断,用户看不到半数持仓
  估值过期。修复:估值类提示单独计数聚合为一行"N 项持仓为手工估值
  (超过 30 天未更新),精确调仓前需先更新金额(前 3 个标的等)",不占
  前 4 上限。
- **P5-3 风险枚举翻译**(`presentation.py` `_risk_trigger_text`):
  "Critical cluster: 1 critical" 等英文内部枚举直出用户面。修复:确定性
  翻译层(长 token 优先,数字保留,未知回退原文不伪造)。
- **P5-4 研判恢复提示**(`advisory_mainline.py`):行情过旧/快照过旧
  的 unavailable message 补"行情恢复后自动重试 / 下次定时窗口自动刷新"。
- **P5-5 候选名单**(`build_push_payload.py` §4):"另有 N 个候选"不再
  空泛,列出候选名称(前 6 个 + 等 N 个)。
- **P5-6 随 P5-1 覆盖**:明日计划与暂缓区不再矛盾重复。
- **P5-7 文案去重**(`presentation.py`):`_deferred_action_text` 已含
  label,不再重复拼接("XXX:XXX:暂缓执行" → "XXX:暂缓执行")。

验证:真实重放 8/6 19:49 cn_after_close——明日计划 512480 显示
"② 暂缓执行,等待数据恢复"(与指令卡一致)、"13 项持仓为手工估值"
聚合行、5 个候选名单齐全、"行情恢复后自动重试"提示。新增 7 个回归
测试(明日计划 gate 2 + 翻译 2 + 聚合 1 + 名单 1 + 文案 1)。



## 2026-08-06 第四轮对抗性校验全量修复（`5e977bd` + 本批未提交）

**Full pytest 1368 passed, 7 skipped, 1 deselected（仅排除既有基线失败
`test_advice_feedback::TestLedgerWrite::test_engine_rollup_reads_ledger`）；
ruff/compileall clean；新增 9 个回归测试
（tests/engine/test_report_defects_p234.py + test_engine + test_scheduled_analysis）。**

### 四轮校验背景

对抗性校验（数字间/层间自洽交叉验证）从 8/5、8/6 生产 artifact 深挖出
四个家族的系统性缺陷——不是单一 bug,而是"数据时间基准不统一 + 门控/
标注只看一层"在数据层、覆盖层、估值层、研判层的反复表现:

- **时间戳族**:P2-1(门控层,已随 5e977bd 修)、P2-2(覆盖层)、P3-3/P3-4(估值层)
- **双写族**:P3-1(双份 cash_schedule)、P2-5(backfill 重复)
- **fail-closed 呈现族**:P2-3(汇率失败无提示)、P4-1b(macro 节点自相矛盾)
- **研判族**:P4-1(宏观数据滞后仍给"置信中")

### Fix — 本批 9 项（工作区未提交,commit 待授权）

- **P2-2 scan 池行情覆盖缺口**（`engine/__init__.py`）:quotes 只拉持仓/
  核心(11 个),scan 池(27 个)行情从不实时拉取,K 线停在 warm 首次拉取日
  (7/23 断档)。引入**定期 stale 刷新**:`_history_last_refresh` +
  `history_refresh_interval_hours`(默认 12h),每次 build 距上次刷新超期
  即对 scan 池重跑 warm_history_cache(过时 K 线被 stale_days 强制重拉,
  新鲜量足走 skipped_cached,成本可控)。
- **P3-4 估值三套时间基准混用**（`context_builder.py`）:持仓估值分三类
  (A股 8/6 实时 / 美股 8/5 / 支付宝基金·银行理财 7/3-8/4),混入同一资金
  数字。新增 `_valuation_age_days` + `stale_manual_mid` 标记(7-30 天),
  数据边界新增 `valuation_age` issue,data_notes 呈现"估值为 N 天前,与
  当日行情混算,金额为近似值"。
- **P3-1 双份 cash_schedule 不一致**（`scheduled_analysis.py`）:顶层
  build_cash_schedule(空 approved_sales)得到毛值 523,472,裁决器内部用
  真实 sales 重算得净额 486,622,双写漂移。修复:顶层用裁决器结果覆盖。
- **P2-5 history_backfill 重复项**（`engine/__init__.py`）:warm_targets
  = instruments + scan_instruments 有交集(5 个标的各出现 2 次)。
  `_dedupe_instruments` 去重。
- **P2-3 汇率失败资金无提示**（`presentation.py` `_data_notes`）:HKD
  84.46 转换失败(asset_completeness=blocked)但 data_notes 无提示,资金
  按 0 计入。修复:data_notes 消费 asset_completeness 的 blocked +
  valuation_age/valuation issue。
- **P4-1b macro as_of 与 age_seconds 自相矛盾**（`context_builder.py`）:
  as_of 取最老字段(6/1)但 age_seconds 取市场层(7/31,6.3 天)。修复:
  两者同源,均取最老 as_of。
- **P4-1 研判用 6/1 宏观数据给"置信中"**（`unified_snapshot.py` +
  `context_builder.py`）:macro fact 的 as_of 伪造为当前时间;官方统计以
  裸文本进 raw_prompt 无时间戳。修复:fact as_of 用 field_sources 真实
  时间戳 + 跳过分层元数据键;raw_prompt 宏观区块标注"数据时点 YYYY-MM-DD"。
- **P2-4 风险状态基准双轨**（`presentation.py` + `scheduled_analysis.py`）:
  window_delta 的 level 迁移(session 级)与 transition_key(observation 级)
  并存产生"降风险→对冲/高风险"+"与上次持平"矛盾。修复:user_view.risk
  增加 `window_level_change`(窗口级迁移文本),渲染层可消歧;build_user_view
  移到 window_delta 计算后调用(保持恰好一次)。
- **P3-3 跨报告估值异常**（前 23/27 持仓纹丝不动）:系 P2-2 后果(scan 池
  持仓 K 线停更),由 P2-2 修复覆盖,无需独立代码。
- **P3-2 资金桶加总可审计性**:**确认已由 P1-8 覆盖**——cash_view 已含
  `safety_buffer` 字段,push_payload 已渲染"安全垫 ¥76,321(不计入可用)",
  加总关系可验证。无新代码。

### 验证

- 全量 `1368 passed, 7 skipped, 1 deselected`(新增 9 个回归测试:P2-2
  定期刷新、P3-1 双写一致、P3-4 mid-stale 呈现、P2-3 blocked issue 呈现、
  P4-1 fact as_of、P4-1b age 同源、P2-4 窗口迁移文本等);
- ruff/compileall clean;
- 生产重放与 NAS 同步:待 commit/push 后执行。

### 既有基线

- `test_advice_feedback::TestLedgerWrite::test_engine_rollup_reads_ledger`
  仍失败(git stash 证实 HEAD 31d2fff 即失败,与校验无关,按仓库纪律不
  扩 scope,记录)。



**Full pytest 1356 passed, 7 skipped, 0 failed；ruff/compileall clean；
NAS 8/5 `us_after_close` artifact 重放渲染 15 项断言全 PASS；已同步 NAS
生产并修复其 cron 权限故障（root 属主文件 → hermes）。**

### Fix 1 — 六类根因缺陷（2026-08-05 盘后报告实测暴露）

R1–R6 全部为确定性渲染/门控代码缺陷，非 LLM 幻觉：

- **R1 大类约束渲染成单票占比**（`presentation.py` `_conflict_reason`）：
  `bucket_ratio` 是权益大类占比（0.127）却被挂到单票名下，报告显示
  "NVDA 当前占组合12.7%"。改为"NVIDIA：触发止盈信号，但权益大类当前
  占组合12.7%（低于下限25%）"，保留 `label（code）：` 前缀兼容解析。
- **R2 行情过时仍输出精确金额**（`presentation.py` + `build_push_payload.py`）：
  被 gate 拒绝的动作在行情 stale 时不再携带 `estimated_amount_cny`，渲染
  "行情数据过时，金额待数据恢复后确认"（fail-closed）。黄金 ¥36,850
  NAS 口径自洽（30%×122,833），缺陷是 stale 手工估值仍输出精确金额。
- **R3 风险标签自相矛盾**（`presentation.py`）：level "降风险" + transition
  "风险升级"拼贴矛盾 → transition 改相对语义"较上次升级/较上次缓和/
  与上次持平"。
- **R4 "无新证据"与"风险升级"并存**（`build_push_payload.py`）：
  `_section_window_changes` 只查 outlook_delta → payload 新增 `window_delta`
  字段 + `_window_delta_human_changes()` 消费确定性变化（风险档位迁移/
  动作调整/冲突新增），first_in_session 且风险刚升级时也显示风险档位变化。
- **R5 research 候选不过行情新鲜度 gate**（`scheduled_analysis.py` +
  `presentation.py`）：A股数据过时仍给精确价格/布局建议 → 新增
  `data_quality` 门控，stale 候选降级"观察"、reasons 替换为数据边界说明。
- **R6 suspend 仍给布局建议**（同上）：`suspend_accumulation` 时加仓类
  候选 sizing_hint 降级"仅观察"，setup_tag 降级"观察"。

### Fix 2 — 标签映射收敛（去硬编码，`a8dd57d`）

- `presentation.py` 新增模块级 `TRANSITION_LABELS` / `STALE_FRESHNESS`
  单一权威；risk 结构保留 `transition_key` 原始枚举。
- `build_push_payload.py` 镜像映射值同步 + 注释（刻意不依赖引擎层）；
  渲染按枚举分支而非比较中文字符串。
- `scheduled_analysis.py` 复用 `STALE_FRESHNESS`；`_ACCUMULATION_SIGNALS`
  提升为模块级常量。
- 修复：同一 escalated 在报告两处渲染"升级"与"较上次升级"并存的不一致。

### 生产环境运维（同会话完成）

NAS `/mnt/user/code-project/stocks-claw` 权限故障修复：8/5 20:59 起
root 属主文件阻塞 hermes 用户 cron（PermissionError，报告停更于 8/5
21:19）→ 全部 chown hermes:users，cron 恢复正常。8/5 原 artifact 已
恢复（强制重跑为 degraded 不覆盖生产数据）。

Known limitation (honest): 修复从下一份真实报告起生效；8/5 已推送的
报告无法撤回。LLM 研判层未改动（数据层缺陷，待行情源恢复）。

## M4 — Constraint model upgrade (landed 2026-08-04)

**Full pytest 1351 passed, 7 skipped, 0 failed；ruff/compileall/diff-check
clean。** Requirements rationale:
`docs/analysis/user-requirements-analysis-2026-08-04.md`（§8 校验发现：
限购/封闭池/换汇三大硬约束此前只存在于系统外的一份 Markdown 报告，
系统金融记忆零表达）。

What landed:

- **`stocks/engine/constraint_model.py`（新）**：M4 schema + fail-closed
  校验（未知键/错误类型/引用未定义池 → `ConstraintConfigError`）+
  `ConstraintModel` 运行时视图 + `iter_bucket_rules`（legacy 桶规则
  唯一迭代入口）。Schema：`pools`（含 `isolated`）/
  `position_pool`/`account_pool`/`bucket_limits`（分池比例）/
  `hard_caps`（`on_breach: must_reduce`）/`position_restrictions`
  （`no_buyback` + `restriction_note`）。`_` 前缀键为文档键。
- **约束即金融记忆**：`StocksEngine._load_constraints` 改为
  `.local/portfolio_constraints.json` 优先，repo
  `stocks/config/portfolio_constraints.json` 降级为中性 schema example
  （分发不携带任何用户约束值）。用户的真实约束（QDII 限购不可逆 +
  12% 硬上限、IBKR 海外封闭池、分池比例）已按用户声明写入 `.local/`。
- **裁决器**（`portfolio_adjudicator`）：止盈作用于 no_buyback 持仓
  → 抑制并注明不可逆；任何卖出作用于 no_buyback 持仓 →
  `decision_reason` 附加 `⚠️ 不可逆` 警告原文；硬上限超限 → 无技术
  信号也产出指明上限的强制减仓候选（按超出额计算比例）；定义
  `bucket_limits` 时启用分池比例检查（legacy 全局检查自动关闭，永不
  双重执行），冲突携带 `pool`/`pool_label`；`PortfolioAction.pool`
  新字段；`cash_schedule.pools` 分池现金计划（隔离池独立安全垫）。
- **分池资金**（`_build_capital_allocation`）：加仓候选携带
  `pool`/`pool_label`/`funding_deployable_cny`（仅本池可动用），输出
  `pools` 分池资金段；隔离池现金与回款不跨池出资。
- **既有消费者防护**：`scaffolds` 偏离检查与 `context_builder` 的
  【约束配置】prompt 段改经 `iter_bucket_rules`，M4 扩展键不再被
  误读为幻影桶（修复 `list object has no attribute get`）。
- **验证**：13 个新测试（schema 校验 10 + 模型 7 + 裁决器硬上限/
  不可逆/分池 5 + 跨池出资围栏 1，含任务文件全部验收标准）；真实
  `cn_after_close` 烟雾（2026-08-04）：报告首次给出三个带数量/金额的
  可执行动作（512480 止损 5000 股、510300 减仓 525 股、588000 减仓
  450 股），冲突带"国内池："前缀，NVDA 减仓不再被国内池权益低配
  阻塞（分池语义生效）；备用模型链在真实运行中触发（gpt-5.5 失败后
  尝试 deepseek-v4-pro）。

Known limitations (recorded honestly):

- 分池比例检查不含 replacement-chain 自动换仓（该机制仍是 legacy
  全局路径）；分池模式下低配+减仓走人工确认冲突。
- `cn_equity`/`csi300` 等标签未映射到桶（`_TAG_TO_BUCKET` 数据缺口，
  M4 前已存在）：510300/588000 不参与权益桶比例，桶覆盖率是既有
  数据质量问题，未在本任务扩大。
- 硬上限当前 nasdaq100 占比 ~9% < 12%，未触发强制减仓；已用测试
  覆盖触发路径。

## 2026-08-03 三连修复（评审驱动，`v2.9-p0-fixes`）

**Committed this session（见 git log）；full pytest 1327 passed, 7 skipped,
0 failed；ruff/compileall/diff-check clean。**

### Fix 1 — institution_type 链路（`no settlement rule matched` 根因）

Real 2026-08-03 reports showed every A股 (cn_broker) action stuck at
manual_review with `no settlement rule matched` and `待确认平台`. Root
cause chain: `ContextBuilder._value_position` never put account metadata
on `position_valuations` items, so all downstream consumers
(`_build_action_cards`, `finalize_decision`) fell back to
`_ACCOUNT_ID_TO_INSTITUTION` in `scheduled_analysis.py` — a hardcoded map
still holding pre-2026-07-06 account IDs (`a_stock`/`boc_life`), while the
live assets file uses `cn_broker`/`bochk_life`. With `institution_type=""`
no rule in `engine.yaml execution_rules.settlement_rules` can match
(fail-closed by design).

Fix: `context_builder.py` threads `asset_accounts_v2` (authoritative
accounts section) into `_build_position_valuations`/`_value_position` —
each item now carries `account: {account_id, display_name,
institution_type, type}` (`{}` when unmatched). `scheduled_analysis.py`
map updated as backstop (`cn_broker`/`bochk_life` added; legacy IDs kept);
`_platform_display` maps `cn_broker` → `A股证券账户`. Smoke: real
cn_after_close re-run — 23/23 positions carry institution_type, 512480
stop_loss resolves `executable_quantity 5000` / `¥4,605`;
`no settlement rule matched` gone from user_view.

### Fix 2 — LLM 超时重试 + 备用模型链（P0，评审 P0-1）

2026-08-03 cn_after_close 真实运行 `synthesis error: timed out` →
走势研判缺席。原配置单次调用、180s 超时、无重试、无备用模型。
`advisory_mainline.build_advisory_outlook` 现支持：
`llm.outlook.retry_attempts`（engine.yaml=1，默认 0）每模型重试 +
`llm.outlook.fallback_models`（engine.yaml=[deepseek-v4-pro,
deepseek-v4-flash]）共用端点/密钥按序降级。仅传输/解析失败（确定性
hold_default fallback）触发下一尝试；校验通过的研判不再重试；
全部失败仍诚实降级"研判待复核"。`resolve_mainline_llm_client`
保留（asset_intake_service 使用），新增 `resolve_mainline_llm_clients`。

### Fix 3 — 风险状态路径锚定（P0，评审 P0-2 修正版）

评审原结论"风险状态滞留 19 天"经深夜复核**不成立**：运行产物链证明
TTL 自动解除机制工作正常（7-31 hedge → 8-03 06:08 过期重置 normal →
15:12 新 critical 簇重新 escalate → 21:47 再次过期降级）。盘后
"地缘政治 crisis"来自当天新鲜情报簇。真实缺陷：
`_persist_risk_state` 用进程 CWD 解析相对 `state_path`/`artifact_dir`，
与 `resolve_artifact_dir`（锚定 repo_root）不一致——cron/agent 不同
工作目录启动会把风险状态静默分裂成多份。新增
`_resolve_risk_state_path` 锚定仓库根目录；`load()`/`_update_locked()`
的 expires_at 逻辑未动。

### 评审文档

`docs/analysis/report-usability-review-2026-08-03.md`：报告可用性与
LLM 能力发挥度全面评审（含 P0-2 修正记录）。结论：管线能力已建成，
LLM 成功时报告可用可参考；剩余 P1（情报层 0 方向信号、资产/宏观数据
陈旧）与 P2（M4 约束模型重估、推送自动化、反馈闭环启用）未动。

## Baseline (as of 2026-08-01, sixth verification)

- HEAD (code baseline, verified): `83e94ec` — "feat(M3): advice feedback
  loop — marks, weekly rollup, snapshot reflow"
- Branch: `master` == `origin/master`
- Working tree: **clean** (verified this session — `git status --short --branch`)
- Full pytest: **1317 passed, 7 skipped, 0 failed** (verified on `83e94ec`;
  1315 unit + 2 integration)
- ruff: **clean**; compileall: **clean**; git diff --check: **clean**
- Smoke (M3): real advice ledger read-only (1 record, unmarked, honest
  zero-state rollup); sandbox write path — mark lands via
  `--advice-feedback latest accepted --confirmed`, rollup reflects it,
  real `.local/advice/` untouched.
- Smoke (M2, configured-endpoint case — **live LLM verified 2026-07-31**):
  real `us_post_open` run (`20260731T135500Z`) → `structured_outlook.status
  == "ok"`, near/medium-term judgments with 验证/证伪 lines rendered in
  走势研判, `advisory_receipt.status == "ok"`, 5 source_refs. Advisory
  prompt now requires Simplified Chinese free-text (fixed at `c313d22`).
- Smoke (A1): draft stage against the real assets file writes nothing
  (LLM 429 → ambiguities fallback, as designed); confirm flow verified on
  a sandbox copy — position added, cash delta applied, timestamped backup
  created; real `.local/financial_assets.json` hash unchanged.
- Tag: `v2.8-e1e2-complete` remains at `cc1eaa0` (not an ancestor of HEAD;
  left untouched — re-tagging is the user's call).

## De-hardcode landing (verified 2026-07-31)

Three commits landed and were verified this session:

- `1ab7dec` — M1-M5+M10: config-driven data sources, market prefixes,
  sessions, FX, signal thresholds (21 files).
- `7a60aac` — S1-S4/M1-M12/L1-L5: config-driven providers, sessions, FX,
  risk, quant thresholds, intelligence mappings (27 files).
- `03ee449` — follow-up fix wiring `portfolio_layering.min_add_amount_cny`.

Behavior preservation was verified by direct old-vs-new value comparison
(not just by tests): all `market_events` keyword/sentiment tables,
`quant_action` mapping tables (signal proxy / theme→exposure / tag→bucket),
`intelligence_analyzer` tables (theme markets / category maps / symbol
tables), all 15 signal thresholds and rank weights, and the USD/CNY 7.2
fallback are **identical** to the previous hardcoded values. Hardcoded
internal LLM fallback URLs were removed from shipped config; the outlook
path fails closed (unavailable) when no endpoint is configured.

Note: the S/M/L finding IDs in the commit messages are not traceable to
any repo document (`docs/analysis/system-consistency-review-2026-07-30.md`
uses P0/P1/P2 numbering) — treat the commit messages as the only coverage
claim. Defaults now live in two places (`DEFAULT_ENGINE_CONFIG` and
module-level fallback constants); keep them in sync when tuning.

## What's actually running in production

The push path is:

```
StocksEngine.build_context
  → AnalysisContext v12
  → Technical / Rotation / QuantAction / Factor Rules
  → Portfolio Adjudicator → user_view (instruction_card + assistant_brief)
  → Advisory Mainline (primary sessions): snapshot → LLM analyst →
    validation receipt → structured_outlook (研判待复核 on any failure)
  → Deterministic Renderer (6-section concise report)
  → validate_push_truth + validate_payload_text
  → Feishu delivery
```

E1 truth gate and E2 concise renderer are landed and verified. Details on
what each covers are in the archived task files under
`docs/archive/tasks-completed-2026-07/` for the record.

**Daily scheduling is live (2026-08-03):** three Kimi Work 定时任务
(cron, `Asia/Shanghai`) drive the production sessions — A股
`7 10,15 * * *`（cn_post_open + cn_after_close）、美股盘前
`47 21 * * *`（us_post_open）、美股盘后 `47 5 * * *`（us_after_close）。
Each run executes `--scheduled-run-due`（周末/休市自动跳过）并为新 run
渲染推送 payload 到 `.local/push_payloads/<date>/`。执行路径已验证
（手动触发 run `run_89cf79ea` succeeded，workspace 绑定正确）。在此之前
没有任何系统级调度器（crontab / LaunchAgents 均无），分析只会手动触发。

## Known gaps against `stocks/VISION.md` §2.3 (re-scored after M2, 2026-07-31)

M2 landed at `7c35c7f`: the advisory mainline now produces the 走势研判
content from the LLM Investment Analyst (short-term 3-7天 / medium-term
1-3个月 judgments with 验证/证伪 lines, scenarios, source_refs), gated by
quote freshness, snapshot age, client configuration, and the validation
receipt. Verified with fake-client tests and the no-endpoint smoke; a
live-LLM run is still pending (no endpoint configured locally).

Coverage of VISION §2.3's seven required questions:

| # | VISION requirement | Current coverage |
|---|---|---|
| 1 | Market state, drivers, conflicts | ✅ pipeline landed (advisory outlook summary + drivers; honest 研判待复核 fallback when gated) |
| 2 | Position actions, magnitude, condition, reason | ✅ covered (manual-review conflicts now carry 参考: ratio / 参考数量 / 参考金额 audit lines) |
| 3 | Post-trade portfolio/cash/risk delta | ✅ covered (post_trade_projection renders as 执行后估算 line when executable actions exist) |
| 4 | Short/medium-term scenarios with validation/falsification | ✅ pipeline landed (near/medium-term + 验证/证伪 lines + base/bull/risk scenarios; falsification is a hard validation error) |
| 5 | Watch / setup candidates | ✅ covered (提前布局 first-class section, top 2-3 by score + overflow tail) |
| 6 | Data unreliability & suspend condition | ✅ covered (capital-gap data_notes reach push as 待决事项 lines) |
| 7 | Next check condition | ✅ covered |

**Score after M2: 7 full / 0 partial / 0 missing** (was 5 / 1 / 1) — at
pipeline level. Content-level verification (live LLM judgments in real
reports) remains gated on an ad-hoc endpoint and the shadow/user-value
gates below.

## Report mode

Production push runs the **M2 advisory mainline by default** for primary
sessions: `build_unified_snapshot` → `synthesize_advisory` (LLM Investment
Analyst) → `validate_advisory` → projection into `structured_outlook`,
orchestrated by `stocks/engine/advisory_mainline.py`. Every failure path
(stale/missing quotes, snapshot older than 90 minutes, unconfigured LLM
endpoint, `hold_default` fallback, receipt errors) degrades to an honest
`研判待复核` unavailable outlook — never a fabricated judgment.

Toggle: `llm.advisory_mainline.enabled` (default `true` in
`DEFAULT_ENGINE_CONFIG`). Setting it `false` restores the legacy
constrained `OutlookSynthesizer` path (evidence + hash metadata included).
Advisory contracts (`UnifiedAnalysisSnapshot`, `InvestmentAdvisory`,
`AdvisoryValidationReceipt`) are PRODUCTION as of M2; `AdvisoryShadowRun`
stays SHADOW. Rule engine adjudicator and instruction_card actions are
untouched: advisory informs judgment only, never action selection.

**Financial-memory write surface (A1, landed `c313d22`):** conversational
asset updates go through `--asset-intake "自然语言"` (draft + confirmation
token, never writes) → `--asset-intake-confirm --draft-json --token`
(token-validated, ambiguity-free drafts only, timestamped backup, v2
`Position`/`Account` validation before persist). Direct hand-edits of
`.local/financial_assets.json` bypass this audit path and are unsupported;
legacy v1 CRUD remains disabled on v2 files.

## Roadmap now: M1 ✅ → M2 ✅ → A1 ✅ → M3 ✅ → W1 (+ D1, M4 backlog)

Full description in `ROADMAP.md` (M5 advisory-terminal milestone added
2026-08-01). Rationale in `docs/analysis/direction-2026-07-31.md`.

- **M1 — Report structure upgrade. ✅ landed 2026-07-31 (`382207b`).**
- **M2 — Outlook mainline. ✅ landed 2026-07-31 (`7c35c7f`).**
  Advisory mainline drives primary-session `structured_outlook`;
  **live-LLM verified same day**. VISION §2.3 score 5/1/1 → 7/0/0
  (pipeline level).
- **A1 (M5) — Asset intake entry. ✅ landed 2026-08-01 (`c313d22`).**
- **M3 — Feedback loop. ✅ landed 2026-08-01 (`83e94ec`).**
  `--advice-feedback REF accepted|partial|rejected|deferred [--note]
  --confirmed` marks the advice ledger (model-validated in-place rewrite,
  ambiguous/unknown refs rejected); `--advice-rollup [DAYS]` summarizes
  the window (acceptance rate, rejection notes, unmarked nudge); marked
  outcomes flow into `UnifiedAnalysisSnapshot` as `advice_outcome` /
  `advice_feedback_rollup_7d` facts — evidence for the next Outlook run,
  never an auto-tuner.
- **W1 (M5) — Watchlist productization.** User-designated instruments
  persisted, scanned daily, surfaced in push. **This is the next task.**
- **D1 (M5) — US quotes freshness verification.** Finnhub key present;
  the 2026-07-31 live us_post_open run produced fresh quotes — formal
  verification folded into W1 (US quote path).
- **M4 — Constraint model upgrade (backlog candidate, added 2026-07-31).**
  Irreversibility (no-buyback), segregated pools, hard caps. Stays backlog
  until constraint-driven advice errors show up in real reports.

## Deprecated / removed

- ~~TASK-002 (AdviceRecord draft writer)~~ — retired as scoped. Its
  legitimate scope (feedback ledger) is folded into M3.
- ~~TASK-003 (execution adapter + mock sink)~~ — retired. User places
  orders themselves; no execution surface required.
- ~~TASK-004 (E2E smoke from payload to receipt)~~ — retired. Downstream
  of TASK-003, no longer meaningful.
- ~~TASK-005 (A2/A5 migration audit)~~ — retired as a standalone task;
  migration is M2 itself.
- ~~`EXECUTION_PLAN.md`~~ — deleted; content had already been reduced to a
  redirect stub. `docs/tasks/` is the sole active task list.

## Live/user-value gates (retained from previous ROADMAP)

Both retained as verification concepts for M2; **status after M2 landing:**

- **Shadow gate — PENDING.** The 5-consecutive-trading-day replay of live
  main-window runs cannot be satisfied by a local one-off run; it requires
  real trading days with a configured LLM endpoint. Recorded as pending,
  not waived.
- **User-value gate — PENDING.** User confirms the new capability reduces
  decision cost before M2 fully replaces the legacy outlook (the legacy
  path remains available via `llm.advisory_mainline.enabled: false`).

## Advisory pipeline — status after M2

- `advisory_mainline.py` (new, M2) orchestrates the production advisory
  path for primary sessions; `UnifiedAnalysisSnapshot` /
  `InvestmentAdvisory` / `AdvisoryValidationReceipt` are PRODUCTION
  contracts (see `docs/contracts/README.md`).
- Shadow tooling (`advisory_shadow_store.py`, `run_shadow_advisory.py`,
  `compare_advisory_paths.py`, artifacts under `.local/advisory_shadow/`)
  remains available for the shadow-gate replay; `AdvisoryShadowRun` stays
  SHADOW.
- Asset-intake (`asset_intake_parser.py`, `llm_asset_intake.py`,
  `asset_intake_writer.py`) remains library-only (SHADOW), unchanged by M2.

## Next concrete task

M3 is done. Next is **W1 — Watchlist productization** (M5; task file to be
written before starting): user-designated instruments persisted as
financial memory, pulled into daily context + action-signal scans,
surfaced in push — with D1 (US quotes freshness verification) folded in
since W1 exercises the US quote path. `TASK-M4-constraint-model-upgrade.md`
stays backlog.

Known pending items (recorded honestly, not waived):
- **Shadow gate** (M2): 5 consecutive trading days of live main-window
  advisory runs — live runs: 2026-07-31 us_post_open、2026-08-03
  cn_post_open（中文研判，receipt ok）。每日定时任务已于 2026-08-03
  上线，后续天数自动积累。
- **User-value gate** (M2/M3): user confirms reduced decision cost.
- **MCP wiring for A1 intake + M3 feedback** (CLI landed first; MCP is
  the agent's primary surface — natural follow-up, no task file yet).
- **Feishu inline feedback buttons** (M3 non-goal; delivery-layer
  follow-up).
- **Feishu push delivery automation**: 推送 payload 已由定时任务每日
  生成；发送到飞书的自动化接线尚未做（此前为手动）。
