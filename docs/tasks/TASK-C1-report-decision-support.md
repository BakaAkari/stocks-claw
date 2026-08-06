# TASK-C1 — 报告决策支持层补全(明日计划 / 冲突解读 / 研判边界降级)

Milestone: **C1**(决策支持层)。来源:2026-08-06 四轮对抗性校验遗留的
三个产品层问题(非缺陷——系统"展示"正确但"决策辅助"不足)。

## Objective

把盘后报告从"信息展示正确"升级为"决策支持可用"。三个工作包共享同一
产品目标:交易员看了报告应该知道**明天做什么、为什么、以及依据是否可信**。

8/6 报告实测缺陷(全部为产品层,数据层/渲染层已在 5e977bd + 5e5fb64 修复):

1. **明日计划缺失**:报告承诺"明日计划"但实际只有"下一检查点"(无具体
   次日行动)。已获批动作、待决事项、风险状态都没有转成可执行的次日清单。
2. **冲突只展示不解读**:科创50 减仓信号 vs 权益大类低配 12.7%<25% 的
   冲突卡只列出矛盾双方,不给裁决倾向(倾向减仓/倾向维持/需人工裁定),
   交易员无法从报告本身得到行动建议。
3. **研判边界不联动降级**:P4-1 已修复 macro fact 时间戳标注(数据时点
   进入 prompt),但**没有自动降级机制**——关键数据(宏观/行情)过旧时
   研判仍给"置信中",报告不主动提示"本研判基于 N 天前数据,可信度降低"。

## Scope

三个工作包全部属于 `stocks/engine/` 与 `scripts/build_push_payload.py`
的确定性渲染/编排层。**不涉及** LLM 研判本身的能力提升、不新增数据源、
不改既有已通过测试的契约字段。

### WP1 — 冲突解读(最独立,先做)

现状:`build_user_view` 的 `assistant.conflict_details` 只投影
`portfolio_decision.unresolved_conflicts`(矛盾双方 + bucket 占比),无结论。

目标:每个 unresolved conflict 增加**确定性解读字段**(不依赖 LLM):

- `tilt`: `"action"` | `"constraint"` | `"manual"`——确定性规则判定倾向。
  规则示例:信号是止损/减仓且 bucket 低于下限 → 倾向维持(bucket 下限优先,
  避免在低配区再降);信号是加仓且 bucket 高于上限 → 倾向不动;其余 → manual。
- `tilt_reason`: 一句中文解释,说明为何倾向该侧(可读,不泄露内部枚举)。

Likely files: `stocks/engine/presentation.py`(`_conflict_detail` +
`_conflict_summary`)、`stocks/engine/portfolio_adjudicator.py`(若裁决器
已留字段)。先在 presentation 层实现,不侵入裁决器。

验收:含"低配+减仓"型冲突的报告,user_view 出现
`conflict_details[].tilt="constraint"` + 中文 tilt_reason;纯展示型冲突
(仅信息提示无信号)为 `tilt="manual"`。

### WP2 — 研判边界自动降级(约束其他所有输出,次做)

现状:P4-1 修复后,宏观/行情时间戳已标注、data_notes 已呈现过时提示,但
`structured_outlook.near_term/medium_term.confidence` 仍是 LLM 给定值,
不与数据新鲜度联动。

目标:在 `advisory_mainline.build_advisory_outlook`(合成后、定稿前)加
**确定性降级层**:

- 输入:context.data_quality(macro.official.freshness、quotes 各 market
  freshness、position_valuations 中 `valuation_age_days` 分布)。
- 规则:关键数据过旧(官方统计 old/stale 或任一主市场 quotes old)且
  LLM confidence=medium/high → 降一级(high→medium,medium→low);
  `data_limitations` 追加"研判基于 N 天前宏观数据,可信度已降级"。
- 降级只影响 confidence 与 limitations,不改 rationale/validation 文本
  (诚实保留 LLM 原话,只调可信度)。

Likely files: `stocks/engine/advisory_mainline.py`、
`stocks/engine/advisory_synthesizer.py`(若降级需回写 advisory)。

验收:用 8/6 旧 artifact 的 data_quality 重放,宏观 old 时
near_term.confidence 从 medium 降为 low,limitations 含降级说明;
数据新鲜时不变。

### WP3 — 明日计划生成(依赖 WP1/WP2 结论,最后做)

现状:报告有 `session_summary.headline` + "下一检查点",无次日行动清单。

目标:确定性生成 `assistant.tomorrow_plan`(不依赖 LLM 创作):

- 输入:approved_actions(含 gate 拒绝但需人工确认项)、
  unresolved_conflicts 的 WP1 tilt、待决资金(¥36,850 类)、
  risk_state(升/降级则提示动作)、structured_outlook(降级则标注低可信)。
- 输出:3-6 条"明日操作项",每条含
  `{action, position, amount_hint, priority, source}`;
  无操作时输出 `[{action: "观察", ...}]` + 一句"明日无新增动作"。
- 明确不承诺收益、不下单,只给人工确认清单。

Likely files: `stocks/engine/presentation.py`(新增 `_tomorrow_plan`)、
`stocks/engine/scheduled_analysis.py`(在 build_user_view 前组装输入)、
`scripts/build_push_payload.py`(渲染为"明日计划"节)。

验收:真实 cn_after_close artifact 重放,payload 含"明日计划"节且
条目可追溯到输入动作/冲突/资金;无动作时输出观察项。

## Non-goals

- 不改 LLM 研判 prompt/能力,不做新的 LLM 调用。
- 不新增数据源、不改资金计算、不动已修复的 P2-x/P3-x 行为。
- 不解决 W1(watchlist 产品化)与"双引擎信息面专项"(见需求分析 §7)。

## Likely files(汇总)

- `stocks/engine/presentation.py`
- `stocks/engine/scheduled_analysis.py`
- `stocks/engine/advisory_mainline.py`
- `stocks/engine/advisory_synthesizer.py`
- `scripts/build_push_payload.py`
- `tests/engine/test_report_defects_p234.py`(追加)或新
  `tests/engine/test_decision_support.py`

## Focused tests

每 WP 至少 3 个:正常路径、边界(无冲突/无动作/数据新鲜)、不回归
(既有 1368 passed 全量)。WP2 需用 8/6 旧 artifact data_quality 重放。

## Smoke check

`python -m stocks.engine.scheduled_analysis --dry-run`(或既有 smoke
命令)后检查 artifact:`user_view.assistant_brief.tomorrow_plan` 存在、
`conflict_details[].tilt` 存在、outlook confidence 与数据新鲜度一致。

## Stop criteria

- 三 WP 全实现,全量 `pytest` 通过(仅保留既有基线失败
  `test_advice_feedback::TestLedgerWrite::test_engine_rollup_reads_ledger`);
- ruff/compileall clean;
- 真实 artifact 重放验证 WP1/WP2/WP3 输出;
- STATUS.md 记录(C1 完成),commit+push+NAS 同步。

## 实施顺序建议

WP1（独立）→ WP2（约束全局）→ WP3（依赖前两者）。每 WP 独立 commit，
避免单 commit 过大。已获用户授权直接完成（2026-08-06 会话）。

## Outcome（2026-08-06 完成）

三 WP 全部实现并验证：

- **WP1 冲突解读**:`presentation.py` 新增 `_conflict_tilt`(确定性规则:
  stop_loss→action / reduce+低配→constraint / 加仓+高配→constraint /
  其余→manual),`_conflict_detail` 增加 `tilt` + `tilt_reason` 字段。
- **WP2 研判边界降级**:`advisory_mainline.py` 新增 `_apply_freshness_downgrade`
  (macro 官方统计或行情 old/stale 时 high→medium→low 降级,不改 rationale,
  data_limitations 追加"可信度已自动降级"),接入 `build_advisory_outlook` 定稿前。
- **WP3 明日计划**:`presentation.py` 新增 `_tomorrow_plan`(确定性清单:
  approved_actions + conflict tilt + data_notes + risk_state + outlook 低可信,
  输入可追溯,不暴露内部 position_id),`build_push_payload.py` §6 后渲染
  "明日计划"节(①/②/③ 优先级标记)。

验证:全量 `1380 passed, 7 skipped, 1 deselected`(既有基线失败);
新增 12 个回归测试(test_report_defects_p234.py,现共 19 个);ruff/compileall
clean;已 commit+push+NAS 同步。commit: 见 git log(C1 相关)。
