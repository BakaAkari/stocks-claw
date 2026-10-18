# docs/contracts/ — contract lifecycle index

The only place that records **which contract is actually live**. Full field-
level schema detail lives in `../../stocks/DATA_MODEL.md` (current
production and reference schema) and `legacy-reference.md` (superseded
detail kept for history). This file does not duplicate that detail — it
tells you, per contract, what stage it's at, where it's implemented, and who
actually consumes it, verified against code rather than plan intent.

## Labels

- **PRODUCTION** — the repository's designated production-shaped contract; actual NAS use still requires the operating-model deployment gates.
- **SHADOW** — implemented and running, produces real artifacts, but does
  not reach production push or write financial memory.
- **PLANNED** — schema/types exist in code but nothing produces or consumes
  them in a real run yet.
- **LOCAL** — implemented and exercised in the current checkout; it is not yet the designated deployed contract. Financial-memory writes still require confirmation.
- **DEPRECATED** — superseded by a newer contract; code/tests may still
  exist but it is not part of the current target direction.

Lifecycle labels describe the checkout's code role, not deployment status.
Use [Operating Model v1](operating-model-v1.md) for the L0-L5 readiness levels.
A `LOCAL` contract cannot be treated as NAS production until its real-account,
shadow and delivery gates pass.

## Index

| Contract | Label | Implementation | Consumer |
|---|---|---|---|
| [PersonalMandate / AccountStatement / CashMovement / ExecutionRecord v3 / financial integrity / LLM lineage](financial-trust-v1.md) | LOCAL | `personal_mandate.py`, `financial_reconciliation.py`, `financial_service.py`, `advisory_lineage.py` | Confirmed CLI/MCP facts, read-only reconciliation, main-window proposal validation and source-linked review; no deployment or real-account acceptance implied. |
| [RecommendationSnapshot / AdviceRecord / UserDecisionRecord / ExecutionRecord / DecisionReview v2](decision-review-v2.md) | LOCAL | `scheduled_analysis.py`, `decision_service.py`, `models.py`, `persistence.py`, `outcome_attribution.py`, `decision_observation.py` | Scheduled source capture; confirmed CLI/MCP fact APIs; read-only review and complete decision/fill context; public-price replay smoke. No automatic position updates or investment-performance claim. |
| [SignalEvent / frozen EvaluationTask / EvaluationResult v1](signal-evaluation-v1.md) | SHADOW (manual) | `signal_tracker.py`, `evaluation_task_sync.py`, `evaluation_ledger.py`, `target_observation.py`, `evaluation_contract.py` | Manual collection and frozen-result report; real public-price replay smoke. Raw price observations only; no production feedback or financial-memory writes. |
| `AnalysisContext` v12 | PRODUCTION | `stocks/domain/models.py`, `stocks/engine/context_builder.py` | All adapters (`stocks/adapters/`), scheduled runner |
| `portfolio_decision.user_view` | PRODUCTION | `stocks/engine/presentation.py` | Push renderer (`build_push_payload.py`), Feishu delivery |
| `structured_outlook` v2 / `outlook_delta` v1 | PRODUCTION | `stocks/engine/advisory_mainline.py` (primary sessions, M2), `stocks/engine/outlook_synthesizer.py` (legacy path when `llm.advisory_mainline.enabled: false`), `stocks/engine/outlook_validation.py` | Main-window and observation-window scheduled artifacts |
| `ScheduledAnalysisRun` v1 | PRODUCTION | `stocks/engine/scheduled_analysis.py` | `.local/scheduled_runs/`, `scripts/run_push_report.py` |
| `FinancialAsset` v1 / `Account` / `Position` v2 | PRODUCTION | `stocks/domain/models.py` | Financial memory adapters, `AnalysisContext` builder |
| Legacy `AdviceRecord` / `ExecutionRecord`, `ForecastRecord` | PRODUCTION | `stocks/domain/models.py`, `stocks/engine/` ledger modules, `stocks/engine/advice_feedback.py` | Original confirmed-write adapters and compatibility views. Legacy recommendations/executions are not promoted to the verified v2 chain. |
| Legacy `DecisionSnapshot` / inferred return settlement | DEPRECATED | `outcome_attribution.py` compatibility reader | `legacy_unverified` history only; no old strategy returns or win rates. |
| `data_quality`, `rotation`, `action_signals`, `macro_snapshot` | PRODUCTION | `stocks/engine/context_builder.py` and respective modules | `AnalysisContext`, scheduled runner |
| `UnifiedAnalysisSnapshot` v1 | LOCAL | `stocks/domain/advisory_models.py`, `stocks/engine/unified_snapshot.py` | Local advisory mainline and `scripts/run_shadow_advisory.py`; NAS deployment and real-account acceptance pending |
| `InvestmentAdvisory` v1 | LOCAL | `stocks/domain/advisory_models.py`, `stocks/engine/advisory_contract.py`, `stocks/engine/advisory_synthesizer.py` | Local `stocks/engine/advisory_mainline.py`; online-model and delivery acceptance pending |
| `AdvisoryValidationReceipt` v1 | LOCAL | `stocks/engine/advisory_contract.py` | Local advisory mainline and shadow artifacts; not evidence of production deployment |
| `AdvisoryShadowRun` v1 | SHADOW | `stocks/engine/advisory_shadow_store.py` | Writes to `.local/advisory_shadow/`; read by `scripts/compare_advisory_paths.py` |
| `AssetIntakeDraft` v1 | LOCAL (CLI) | `stocks/engine/asset_intake_parser.py`, `llm_asset_intake.py`, `asset_intake_writer.py`, `asset_intake_service.py` (A1 translation layer) | CLI `--asset-intake` / `--asset-intake-confirm`; MCP natural-language entry pending |
| `portfolio_constraints` v2 (M4) | PRODUCTION | `stocks/engine/constraint_model.py` (schema + validation), loaded `.local/portfolio_constraints.json` first then `stocks/config/portfolio_constraints.json` (example fallback) via `StocksEngine._load_constraints` | `portfolio_adjudicator.adjudicate_portfolio` (pools / hard caps / no_buyback / per-pool bucket limits), `scheduled_analysis._build_capital_allocation` (per-pool funding fence), `scaffolds` drift checks and `context_builder` prompt via `iter_bucket_rules` (legacy buckets only) |
| `DecisionEnvelope` v1 | DEPRECATED | `stocks/domain/models.py` (`DecisionEnvelope` class), `stocks/engine/decision_contract.py` | None — no adapter or runner produces or consumes it (verified by grep). Predates the 2026-07-22 Advisory-direction pivot; not part of `ARCHITECTURE.md`'s target architecture. Kept for its tests only; do not build on it. |

## How to keep this current

When a contract's consumer changes (an adapter starts/stops calling it, a
shadow script starts feeding production), update its row here in the same
task that made the change. Do not update `../../stocks/DATA_MODEL.md` to say
a phase is "done" — phase/status language belongs only in `../../STATUS.md`;
this file and `DATA_MODEL.md` describe what exists and how it's shaped, not
project progress.
