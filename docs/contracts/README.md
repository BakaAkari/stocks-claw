# docs/contracts/ — contract lifecycle index

The only place that records **which contract is actually live**. Full field-
level schema detail lives in `../../stocks/DATA_MODEL.md` (current
production and reference schema) and `legacy-reference.md` (superseded
detail kept for history). This file does not duplicate that detail — it
tells you, per contract, what stage it's at, where it's implemented, and who
actually consumes it, verified against code rather than plan intent.

## Labels

- **PRODUCTION** — consumed by the live push/report path today.
- **SHADOW** — implemented and running, produces real artifacts, but does
  not reach production push or write financial memory.
- **PLANNED** — schema/types exist in code but nothing produces or consumes
  them in a real run yet.
- **DEPRECATED** — superseded by a newer contract; code/tests may still
  exist but it is not part of the current target direction.

## Index

| Contract | Label | Implementation | Consumer |
|---|---|---|---|
| `AnalysisContext` v12 | PRODUCTION | `stocks/domain/models.py`, `stocks/engine/context_builder.py` | All adapters (`stocks/adapters/`), scheduled runner |
| `portfolio_decision.user_view` | PRODUCTION | `stocks/engine/presentation.py` | Push renderer (`build_push_payload.py`), Feishu delivery |
| `structured_outlook` v2 / `outlook_delta` v1 | PRODUCTION | `stocks/engine/advisory_mainline.py` (primary sessions, M2), `stocks/engine/outlook_synthesizer.py` (legacy path when `llm.advisory_mainline.enabled: false`), `stocks/engine/outlook_validation.py` | Main-window and observation-window scheduled artifacts |
| `ScheduledAnalysisRun` v1 | PRODUCTION | `stocks/engine/scheduled_analysis.py` | `.local/scheduled_runs/`, `scripts/run_push_report.py` |
| `FinancialAsset` v1 / `Account` / `Position` v2 | PRODUCTION | `stocks/domain/models.py` | Financial memory adapters, `AnalysisContext` builder |
| `AdviceRecord` / `ExecutionRecord` / `ForecastRecord` / `DecisionSnapshot` | PRODUCTION | `stocks/domain/models.py`, `stocks/engine/` ledger modules | CLI/MCP write adapters, `recent_advice`/`forecast_summary` in `AnalysisContext` |
| `data_quality`, `rotation`, `action_signals`, `macro_snapshot` | PRODUCTION | `stocks/engine/context_builder.py` and respective modules | `AnalysisContext`, scheduled runner |
| `UnifiedAnalysisSnapshot` v1 | PRODUCTION | `stocks/domain/advisory_models.py`, `stocks/engine/unified_snapshot.py` | `stocks/engine/advisory_mainline.py` (primary sessions), `scripts/run_shadow_advisory.py` |
| `InvestmentAdvisory` v1 | PRODUCTION | `stocks/domain/advisory_models.py`, `stocks/engine/advisory_contract.py`, `stocks/engine/advisory_synthesizer.py` | `stocks/engine/advisory_mainline.py` (production `structured_outlook`), shadow scripts |
| `AdvisoryValidationReceipt` v1 | PRODUCTION | `stocks/engine/advisory_contract.py` | `advisory_mainline` gates on receipt errors; stored as `run["advisory_receipt"]`; read by `audit_report_quality.check_advisory_receipt_coverage` |
| `AdvisoryShadowRun` v1 | SHADOW | `stocks/engine/advisory_shadow_store.py` | Writes to `.local/advisory_shadow/`; read by `scripts/compare_advisory_paths.py` |
| `AssetIntakeDraft` v1 | PRODUCTION | `stocks/engine/asset_intake_parser.py`, `llm_asset_intake.py`, `asset_intake_writer.py`, `asset_intake_service.py` (A1 translation layer) | CLI `--asset-intake` / `--asset-intake-confirm` (draft → token → audited v2 write) |
| `DecisionEnvelope` v1 | DEPRECATED | `stocks/domain/models.py` (`DecisionEnvelope` class), `stocks/engine/decision_contract.py` | None — no adapter or runner produces or consumes it (verified by grep). Predates the 2026-07-22 Advisory-direction pivot; not part of `ARCHITECTURE.md`'s target architecture. Kept for its tests only; do not build on it. |

## How to keep this current

When a contract's consumer changes (an adapter starts/stops calling it, a
shadow script starts feeding production), update its row here in the same
task that made the change. Do not update `../../stocks/DATA_MODEL.md` to say
a phase is "done" — phase/status language belongs only in `../../STATUS.md`;
this file and `DATA_MODEL.md` describe what exists and how it's shaped, not
project progress.
