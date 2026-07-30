# docs/contracts/legacy-reference.md — deprecated contract detail

Superseded field-level detail kept for history only. Nothing in this file is
part of the current production or target architecture — see
`README.md` for lifecycle labels (this file only covers contracts already
marked `DEPRECATED` there) and `../../stocks/DATA_MODEL.md` for the current
schema reference. Do not build on anything described here.

## DecisionEnvelope v1 — DEPRECATED

Implementation: `stocks/domain/models.py` (`DecisionEnvelope` class),
`stocks/engine/decision_contract.py`. No adapter or runner produces or
consumes it (verified by grep, see `docs/contracts/README.md`). Predates the
2026-07-22 Advisory-direction pivot recorded in `../../PLAN.md`; not part of
`../../ARCHITECTURE.md`'s target architecture. Kept for its tests only; do
not build on it.

`DecisionEnvelope` 是面向用户建议的统一顶层协议；`AnalysisContext` 仍是证据层，
不再被规划为最终交付物。顶层字段固定为：

- `status ∈ {ok, degraded, setup_required, validation_failed, failed}`
- `mode_requested` 与
  `mode_used ∈ {internal_llm, agent_delegate, deterministic_only}`
- `decision_plan`、`agent_task`、`setup_required`（无值也必须显式为 `null`）
- `quality`、`errors`、`final_analysis_instructions`

三层职责严格分离：确定性引擎只产事实、候选和仓位边界；决策生成器只在该边界内
产生结构化 `DecisionPlan`；用户 Agent 审查数据质量并结合当前对话输出最终自然语言
分析。最终分析不是市场事实，不得反写行情、事件或资产数据。

唯一机器契约位于 `stocks/engine/decision_contract.py`：
`DECISION_ENVELOPE_SCHEMA` 是 JSON Schema 2020-12 描述，
`validate_decision_envelope()` 是不引入运行时依赖的等价本地校验器。prompt 文案不构成
协议，也不能绕过该校验器。
