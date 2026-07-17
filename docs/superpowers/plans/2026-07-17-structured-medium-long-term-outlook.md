# Structured Medium- and Long-Term Outlook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add source-grounded `1–2周` and `1–3个月` outlooks to the four primary trading reports while preserving the deterministic trade-action trust boundary.

**Architecture:** Extend the intelligence digest so event sources survive into the scheduled context, build a whitelisted `outlook_evidence` package, synthesize strict JSON through an injected OpenAI-compatible client, validate it fail-closed, and attach only validated Chinese display fields to `portfolio_decision.user_view.assistant_brief.outlook`. Primary windows call the synthesizer; watch/pre-close windows only render deterministic, deduplicated evidence deltas. The no-agent push script remains the final renderer.

**Tech Stack:** Python 3.11+, stdlib `urllib.request`, dataclasses/typed dictionaries, pytest, Ruff, existing atomic file patterns, existing OpenAI-compatible endpoint configuration.

## Global Constraints

- Full outlook sessions are exactly `cn_pre_open`, `cn_after_close`, `us_pre_open`, `us_after_close`.
- Horizons are exactly `1-2w` and `1-3m`.
- Only `portfolio_decision.approved_actions` may create trade instructions.
- Outlook output must not contain buy/sell/reduce/add/clear instructions, ratios, or CNY amounts.
- Every news-derived claim must cite an authorized source title, URL, source name, and publication time.
- If directional intelligence coverage is below 20% or signal count is zero, sector-view confidence is capped at `low`.
- Primary-market quotes older than one trading day, stale macro data, unverified key news, or anomalies in a top-five position force overall confidence to `low`.
- The synthesizer may lower the deterministic confidence cap but may never raise it.
- Primary-window synthesis failure must not block the trade card; it produces a sanitized unavailable outlook.
- Observation windows never call an LLM.
- Final delivery remains `no_agent=true`, deterministic, Feishu-compatible, and fail-closed for internal tokens and unauthorized numbers.
- No new third-party dependency is added.

---

### Task 1: Preserve source-rich intelligence clusters in AnalysisContext

**Files:**
- Modify: `stocks/engine/context_builder.py:1792-1818`
- Test: `tests/engine/test_context_builder.py`

**Interfaces:**
- Consumes: persisted `EventCluster.to_dict()` fields `cluster_id`, `theme`, `event_type`, `summary`, `articles`, `affected_markets`, `affected_symbols`, `sentiment`, `urgency`, `confidence`, `formed_at`.
- Produces: `intelligence_digest.top_clusters[]` with those same fields; `articles[]` is limited to five source records per cluster and each record keeps only `source`, `title`, `url`, `published_at`.

- [ ] **Step 1: Write the failing source-preservation tests**

Add tests that construct a stored cluster containing two articles and assert:

```python
cluster = digest["top_clusters"][0]
assert cluster["cluster_id"] == "cluster-oil"
assert cluster["formed_at"] == "2026-07-17T08:00:00+00:00"
assert cluster["articles"] == [{
    "source": "Reuters",
    "title": "Oil rises as shipping risk increases",
    "url": "https://example.test/reuters-oil",
    "published_at": "2026-07-17T07:30:00+00:00",
}]
assert "raw_html" not in json.dumps(cluster)
```

Also assert a stale/non-risk-eligible digest still returns `top_clusters == []`.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest -q tests/engine/test_context_builder.py -k "intelligence_digest"`

Expected: FAIL because `cluster_id`, `formed_at`, and `articles` are absent.

- [ ] **Step 3: Add a deterministic article sanitizer**

Implement in `context_builder.py`:

```python
def _public_cluster_articles(articles: list[dict], *, limit: int = 5) -> list[dict]:
    allowed = ("source", "title", "url", "published_at")
    result = []
    for raw in articles or []:
        item = {key: raw.get(key) for key in allowed if raw.get(key) not in (None, "")}
        if item.get("title") and item.get("url") and item.get("published_at"):
            result.append(item)
        if len(result) == limit:
            break
    return result
```

Extend each `top_clusters` item with exact persisted fields and sanitized articles.

- [ ] **Step 4: Verify GREEN and regression safety**

Run:

`uv run pytest -q tests/engine/test_context_builder.py -k "intelligence_digest"`

`uv run ruff check stocks/engine/context_builder.py tests/engine/test_context_builder.py`

Expected: focused tests PASS; Ruff exits 0.

- [ ] **Step 5: Commit**

```bash
git add stocks/engine/context_builder.py tests/engine/test_context_builder.py
git commit -m "feat: preserve source-rich intelligence clusters"
```

### Task 2: Build the whitelisted outlook evidence package and deterministic confidence cap

**Files:**
- Create: `stocks/engine/outlook_evidence.py`
- Create: `tests/engine/test_outlook_evidence.py`

**Interfaces:**
- Consumes: `context: dict`, `run: dict`, `session_id: str`, `generated_at: str`.
- Produces: `build_outlook_evidence(context: dict, run: dict, *, session_id: str, generated_at: str) -> dict`.
- Produces: `compute_confidence_cap(evidence: dict) -> tuple[str, list[str]]`.
- Produces constants `PRIMARY_OUTLOOK_SESSIONS` and `OBSERVATION_OUTLOOK_SESSIONS`.

- [ ] **Step 1: Write failing authorization and filtering tests**

Create fixtures with positions, exposure tags, action signals, rotation, source-rich clusters, directional signals, macro freshness, risk state, and cash schedule. Assert:

```python
evidence = build_outlook_evidence(context, run, session_id="cn_after_close", generated_at=NOW)
assert set(evidence) == {
    "version", "generated_at", "session", "market", "portfolio_snapshot",
    "asset_class_snapshot", "sector_snapshot", "technical_evidence",
    "rotation_evidence", "intelligence_events", "directional_intelligence",
    "macro_evidence", "upcoming_events", "risk_context", "data_boundaries",
    "authorized_instruments", "confidence_cap", "confidence_reasons",
}
assert evidence["intelligence_events"][0]["sources"][0]["source"] == "Reuters"
assert "position_id" not in json.dumps(evidence)
assert all(event["sources"] for event in evidence["intelligence_events"])
```

Add tests proving:

- source-less clusters are dropped and recorded in `data_boundaries.omitted_event_count`;
- only top-five-by-weight positions, conflicts, or event-tag matches enter `portfolio_snapshot.focus_positions`;
- `directional=0` yields confidence cap `low`;
- one single-source event with otherwise current data yields at most `medium`;
- a data anomaly in a top-five position yields `low`.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/engine/test_outlook_evidence.py`

Expected: collection/import FAIL because `stocks.engine.outlook_evidence` does not exist.

- [ ] **Step 3: Implement focused evidence builders**

Implement these exact functions:

Implement the exact public signatures `build_outlook_evidence(context: dict, run: dict, *, session_id: str, generated_at: str) -> dict`, `compute_confidence_cap(evidence: dict) -> tuple[str, list[str]]`, and `evidence_hash(evidence: dict) -> str`. Each function must return the shapes asserted in Step 1; none may return `None` on normal input.

Internal helpers must sanitize instrument labels through `presentation.display_label`, aggregate the four asset classes, limit technical and rotation evidence to eight entries each, map events to `classification.exposure_tags`, and strip all IDs/internal enums from user-facing subobjects. `evidence_hash` uses canonical JSON with volatile `generated_at` removed and SHA-256.

- [ ] **Step 4: Verify GREEN**

Run:

`uv run pytest -q tests/engine/test_outlook_evidence.py`

`uv run ruff check stocks/engine/outlook_evidence.py tests/engine/test_outlook_evidence.py`

Expected: all tests PASS and Ruff exits 0.

- [ ] **Step 5: Commit**

```bash
git add stocks/engine/outlook_evidence.py tests/engine/test_outlook_evidence.py
git commit -m "feat: build authorized outlook evidence"
```

### Task 3: Validate structured outlooks and hostile model output

**Files:**
- Create: `stocks/engine/outlook_validation.py`
- Create: `tests/engine/test_outlook_validation.py`

**Interfaces:**
- Consumes: `outlook: dict`, `evidence: dict`.
- Produces: `validate_structured_outlook(outlook: dict, evidence: dict) -> list[str]`.
- Produces: `sanitize_unavailable_outlook(reasons: list[str], *, generated_at: str) -> dict`.

- [ ] **Step 1: Write failing schema and hostile-output tests**

Define a minimal valid outlook fixture and assert no errors. Then parameterize hostile mutations:

```python
def test_missing_risk_scenario_is_rejected(valid_outlook, evidence):
    valid_outlook["scenarios"].pop("risk")
    assert "missing scenario: risk" in validate_structured_outlook(valid_outlook, evidence)


def test_invented_source_is_rejected(valid_outlook, evidence):
    valid_outlook["source_refs"].append({
        "id": "fake", "source": "Fake", "title": "Invented",
        "url": "https://invented.test", "published_at": NOW,
    })
    assert any("unauthorized source" in item for item in validate_structured_outlook(valid_outlook, evidence))


def test_trade_instruction_is_rejected(valid_outlook, evidence):
    valid_outlook["sector_views"][0]["rationale"] = "建议加仓25%"
    assert any("trade instruction" in item for item in validate_structured_outlook(valid_outlook, evidence))
```

Use separate explicit tests for:

- `position_id=a_510300` internal-token leakage;
- an unauthorized instrument;
- a numeric claim not present in the evidence numeric authority set;
- `confidence="high"` when evidence cap is `low`;
- a scenario missing validation or invalidation;
- a source reference missing title, URL, or publication time.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/engine/test_outlook_validation.py`

Expected: import FAIL because validator module is absent.

- [ ] **Step 3: Implement strict validation**

Implement exact checks for required top-level fields, horizons, scenario keys, source authorization, instrument authorization, confidence ordering, forbidden internal tokens, trade-instruction phrases, and numeric authority. The forbidden action regex must reject `买入|卖出|减仓|加仓|清仓|止损\d|止盈\d|仓位\s*\d|¥|人民币` while allowing descriptive phrases such as `配置风险上升`.

`sanitize_unavailable_outlook()` returns:

```python
{
    "status": "unavailable",
    "generated_at": generated_at,
    "message": "本期研判未通过数据完整性校验，暂不输出",
    "data_limitations": sanitized_reasons[:3],
}
```

- [ ] **Step 4: Verify GREEN**

Run:

`uv run pytest -q tests/engine/test_outlook_validation.py`

`uv run ruff check stocks/engine/outlook_validation.py tests/engine/test_outlook_validation.py`

Expected: all tests PASS and Ruff exits 0.

- [ ] **Step 5: Commit**

```bash
git add stocks/engine/outlook_validation.py tests/engine/test_outlook_validation.py
git commit -m "feat: validate structured outlooks fail closed"
```

### Task 4: Add the constrained OpenAI-compatible outlook synthesizer and cache

**Files:**
- Create: `stocks/engine/outlook_synthesizer.py`
- Create: `stocks/prompts/structured_outlook_prompt.txt`
- Create: `tests/engine/test_outlook_synthesizer.py`
- Modify: `stocks/engine/config_loader.py:61-67`
- Modify: `stocks/config/engine.yaml:84-91`

**Interfaces:**
- Produces class `OutlookSynthesizer(config: dict, *, transport: Callable | None = None)`.
- Produces `generate(evidence: dict, *, now: str) -> dict` returning validated outlook or unavailable outlook.
- Produces `OutlookCache(root: Path)` with `load(session: str)`, `save(session: str, evidence_hash: str, outlook: dict)`, and atomic writes.

- [ ] **Step 1: Write failing transport, parsing, and cache tests**

Use an injected fake transport—never real network in unit tests. Assert:

```python
synth = OutlookSynthesizer(cfg, transport=lambda request: valid_response)
outlook = synth.generate(evidence, now=NOW)
assert outlook["status"] == "ok"
assert captured_request["response_format"]["type"] == "json_object"
assert "position_id" not in json.dumps(captured_request)
```

Add tests for fenced JSON extraction, empty `content` with `reasoning_content`, invalid JSON, HTTP exception, validation failure, evidence-hash cache hit, a 24-hour near-term expiry, and no cache reuse beyond expiry. Assert failed synthesis returns sanitized unavailable data rather than raising.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/engine/test_outlook_synthesizer.py`

Expected: import FAIL because synthesizer module is absent.

- [ ] **Step 3: Implement configuration and client**

Add `llm.outlook` defaults:

```python
"outlook": {
    "enabled": True,
    "model": "deepseek-v4-pro",
    "api_key_env": "OPENAI_COMPATIBLE_API_KEY",
    "base_url_env": "OPENAI_COMPATIBLE_BASE_URL",
    "fallback_base_url": "http://100.121.167.1:8317/v1",
    "timeout_seconds": 120,
    "temperature": 0.2,
    "max_tokens": 3000,
    "cache_dir": ".local/outlook_cache",
}
```

The implementation loads `paths.secret_env_file` when environment variables are absent, sends only the evidence plus strict schema instructions, parses JSON, applies `validate_structured_outlook`, and atomically caches valid output. If the configured model rejects non-1 temperature, retry once with `temperature=1` only when the API response explicitly says that is required.

- [ ] **Step 4: Verify GREEN**

Run:

`uv run pytest -q tests/engine/test_outlook_synthesizer.py`

`uv run ruff check stocks/engine/outlook_synthesizer.py tests/engine/test_outlook_synthesizer.py stocks/engine/config_loader.py`

Expected: all tests PASS; Ruff exits 0.

- [ ] **Step 5: Commit**

```bash
git add stocks/engine/outlook_synthesizer.py stocks/prompts/structured_outlook_prompt.txt tests/engine/test_outlook_synthesizer.py stocks/engine/config_loader.py stocks/config/engine.yaml
git commit -m "feat: synthesize constrained outlook scenarios"
```

### Task 5: Integrate primary-window synthesis and deterministic observation deltas

**Files:**
- Create: `stocks/engine/outlook_delta.py`
- Create: `tests/engine/test_outlook_delta.py`
- Modify: `stocks/engine/scheduled_analysis.py:320-510`
- Modify: `tests/engine/test_scheduled_analysis.py`

**Interfaces:**
- Produces `compute_outlook_delta(previous: dict | None, current: dict | None) -> dict`.
- Produces `OutlookDeltaState(path: Path)` with atomic `should_emit(market: str, delta: dict) -> bool`.
- `ScheduledAnalysisRunner` receives optional `outlook_synthesizer` dependency; when omitted it constructs one from config.

- [ ] **Step 1: Write failing primary/watch integration tests**

Add runner tests with a fake synthesizer counting calls:

```python
result = _run(runner.run_session("cn_after_close", now=now, force=True))
artifact = json.loads(Path(result["paths"]["json_path"]).read_text())
assert fake.calls == 1
assert artifact["structured_outlook"]["status"] == "ok"
assert artifact["portfolio_decision"]["user_view"]["assistant_brief"]["outlook"]["summary"] == "组合研判"
```

For `cn_open_watch`, assert fake calls remain zero. Seed two previous primary outlooks, assert a changed sector direction produces one `outlook_delta`, and a repeated identical delta is suppressed by state. Assert synthesis exceptions still save an artifact whose trade card is intact and whose outlook status is unavailable.

- [ ] **Step 2: Verify RED**

Run:

`uv run pytest -q tests/engine/test_outlook_delta.py tests/engine/test_scheduled_analysis.py -k "outlook"`

Expected: FAIL because delta module and runner integration are absent.

- [ ] **Step 3: Implement integration after portfolio adjudication and before store.save**

Primary path:

```python
evidence = build_outlook_evidence(context_dict, run, session_id=run["session"], generated_at=run["generated_at"])
outlook = await asyncio.to_thread(self.outlook_synthesizer.generate, evidence, now=run["generated_at"])
run["outlook_evidence_meta"] = {"hash": evidence_hash(evidence), "confidence_cap": evidence["confidence_cap"]}
run["structured_outlook"] = outlook
run["portfolio_decision"]["user_view"]["assistant_brief"]["outlook"] = outlook
```

Observation path loads the latest two valid same-market primary outlooks, computes a delta, and attaches only a non-empty, not-yet-emitted delta to `assistant_brief.outlook_delta`. The delta contains no new conclusion: only changed scenario labels, confidence, source IDs, validation/invalidation conditions, and asset/sector directions already present in the two validated primary outlooks.

- [ ] **Step 4: Verify GREEN**

Run:

`uv run pytest -q tests/engine/test_outlook_delta.py tests/engine/test_scheduled_analysis.py -k "outlook"`

`uv run ruff check stocks/engine/outlook_delta.py stocks/engine/scheduled_analysis.py tests/engine/test_outlook_delta.py tests/engine/test_scheduled_analysis.py`

Expected: all focused tests PASS; Ruff exits 0.

- [ ] **Step 5: Commit**

```bash
git add stocks/engine/outlook_delta.py stocks/engine/scheduled_analysis.py tests/engine/test_outlook_delta.py tests/engine/test_scheduled_analysis.py
git commit -m "feat: integrate outlooks into scheduled windows"
```

### Task 6: Extend the deterministic user view and push renderer

**Files:**
- Modify: `stocks/engine/presentation.py:255-360`
- Modify: `scripts/build_push_payload.py:100-201`
- Modify: `tests/engine/test_presentation.py`
- Modify: `tests/test_push_payload.py`
- Modify: `tests/test_run_push_report.py`
- Modify: `scripts/human_readable_scan.py`
- Modify: `tests/test_human_readable_scan.py`

**Interfaces:**
- `build_user_view(portfolio_decision: dict, position_valuations: list[dict], position_reviews: list[dict], research_candidates: list[dict], risk_state: dict, *, data_boundaries: dict | None = None, session_id: str, session_intent: str, structured_outlook: dict | None = None, outlook_delta: dict | None = None) -> dict`.
- `assistant_brief.outlook` contains only already-validated display fields.
- `assistant_brief.outlook_delta` contains only deterministic comparison fields.

- [ ] **Step 1: Write failing display and payload tests**

Assert a primary report includes, in order:

```python
assert "**中长期研判**" in text
assert "**未来1–2周**" in text
assert "**未来1–3个月**" in text
assert "**基准情景**" in text
assert "**乐观情景**" in text
assert "**风险情景**" in text
assert "[Reuters｜Oil rises](https://example.test/reuters-oil)" in text
assert text.index("**交易指令卡**") < text.index("**私人投资助理**") < text.index("**中长期研判**")
```

Assert watch output renders only `**研判变化**`, not the full outlook. Assert unavailable outlook renders its message and limitations without losing the trade card. Add hostile scan fixtures containing an internal ID and unauthorized number inside `assistant_brief.outlook` and assert rejection.

- [ ] **Step 2: Verify RED**

Run:

`uv run pytest -q tests/engine/test_presentation.py tests/test_push_payload.py tests/test_run_push_report.py tests/test_human_readable_scan.py -k "outlook or medium or long"`

Expected: FAIL because no outlook rendering exists.

- [ ] **Step 3: Implement deterministic Chinese rendering**

Add fixed label mappings:

- `supportive → 偏有利`
- `neutral → 中性`
- `adverse → 偏不利`
- `uncertain → 不确定`
- `high → 高`
- `medium → 中`
- `low → 低`

Limit output exactly as specified: four asset classes, five sectors, three drivers per scenario, five sources. Source links use `[来源｜标题](url)` plus publication time. Do not parse numbers from free text. Extend validator/scanner traversal to include the outlook fields already inside `user_view`.

- [ ] **Step 4: Verify GREEN**

Run:

`uv run pytest -q tests/engine/test_presentation.py tests/test_push_payload.py tests/test_run_push_report.py tests/test_human_readable_scan.py`

`uv run ruff check stocks/engine/presentation.py scripts/build_push_payload.py scripts/human_readable_scan.py tests/engine/test_presentation.py tests/test_push_payload.py tests/test_run_push_report.py tests/test_human_readable_scan.py`

Expected: all tests PASS; Ruff exits 0.

- [ ] **Step 5: Commit**

```bash
git add stocks/engine/presentation.py scripts/build_push_payload.py scripts/human_readable_scan.py tests/engine/test_presentation.py tests/test_push_payload.py tests/test_run_push_report.py tests/test_human_readable_scan.py
git commit -m "feat: render medium-term outlooks deterministically"
```

### Task 7: Add report-contract, fail-closed, and forecast-candidate coverage

**Files:**
- Modify: `tests/engine/test_report_contract.py`
- Modify: `tests/test_push_artifact_guard.py`
- Modify: `stocks/engine/forecasts.py`
- Modify: `tests/engine/test_forecasts.py`

**Interfaces:**
- Produces `build_forecast_candidates(outlook: dict) -> list[dict]`.
- Forecast candidates are not persisted; they require existing confirmed `forecast_save` flow.

- [ ] **Step 1: Write failing contract and forecast-candidate tests**

Assert `agent_task` still has only two top-level sections but explicitly licenses `assistant_brief.outlook`. Assert guard accepts a valid outlook and rejects malformed outlook. For forecast candidates:

```python
assert build_forecast_candidates(outlook_without_thresholds) == []
assert build_forecast_candidates(outlook_with_verifiable_claim) == [{
    "statement": "VIX 在 2026-08-01 前高于 25",
    "target": "macro:VIX",
    "metric": "close",
    "comparator": "above",
    "level": 25.0,
    "deadline": "2026-08-01",
    "confidence": "low",
    "source_ref_ids": ["src-vix"],
    "requires_confirmation": True,
}]
```

- [ ] **Step 2: Verify RED**

Run:

`uv run pytest -q tests/engine/test_report_contract.py tests/test_push_artifact_guard.py tests/engine/test_forecasts.py -k "outlook or forecast_candidate"`

Expected: FAIL because contract and builder are absent.

- [ ] **Step 3: Implement minimal candidate extraction and guard rules**

Only explicit structured `forecast_candidates` supplied by a validated outlook are normalized; do not extract thresholds from prose. Reject missing target, comparator, numeric level, deadline, or source refs. Never call persistence here.

- [ ] **Step 4: Verify GREEN**

Run:

`uv run pytest -q tests/engine/test_report_contract.py tests/test_push_artifact_guard.py tests/engine/test_forecasts.py`

`uv run ruff check stocks/engine/forecasts.py tests/engine/test_forecasts.py tests/engine/test_report_contract.py tests/test_push_artifact_guard.py`

Expected: all tests PASS and Ruff exits 0.

- [ ] **Step 5: Commit**

```bash
git add stocks/engine/forecasts.py tests/engine/test_forecasts.py tests/engine/test_report_contract.py tests/test_push_artifact_guard.py
git commit -m "feat: add accountable outlook forecast candidates"
```

### Task 8: Full verification, real-data acceptance, cron deployment, and documentation

**Files:**
- Modify: `AGENT_GUIDE.md`
- Modify: `DATA_MODEL.md`
- Modify: `ARCHITECTURE.md`
- Modify if required: `/opt/data/scripts/stocks-claw-push-*.sh` only to keep the deployed worktree path synchronized; wrapper behavior must remain deterministic.

**Interfaces:**
- Real artifacts expose `structured_outlook`, `outlook_evidence_meta`, and licensed `assistant_brief.outlook`/`outlook_delta`.

- [ ] **Step 1: Run complete automated verification**

Run:

`uv run pytest -q`

`uv run ruff check .`

`uv run python -m compileall -q stocks scripts`

Expected: zero failed tests, Ruff exit 0, compileall exit 0.

- [ ] **Step 2: Verify the configured external model endpoint without printing credentials**

Load `/opt/data/.env` through a short Python script, call `/models` or one minimal JSON completion using the configured outlook model, and print only HTTP status, model name, and whether valid JSON was returned. Expected: HTTP 200. If unavailable, record the exact blocker and continue with injected-fixture acceptance; do not fabricate a live outlook.

- [ ] **Step 3: Force-run all four primary sessions**

Run with current valid market dates/times and `--force`:

```bash
uv run python -m stocks.adapters.cli --scheduled-run-session cn_pre_open --now '2026-07-17T08:50:00+08:00' --force
uv run python -m stocks.adapters.cli --scheduled-run-session cn_after_close --now '2026-07-17T15:20:00+08:00' --force
uv run python -m stocks.adapters.cli --scheduled-run-session us_pre_open --now '2026-07-17T21:00:00+08:00' --force
uv run python -m stocks.adapters.cli --scheduled-run-session us_after_close --now '2026-07-18T04:20:00+08:00' --force
```

For each artifact assert with a Python audit script:

```python
assert outlook["status"] in {"ok", "unavailable"}
if outlook["status"] == "ok":
    assert set(outlook["scenarios"]) == {"base", "bull", "risk"}
    assert outlook["near_term"]["horizon"] == "1-2w"
    assert outlook["medium_term"]["horizon"] == "1-3m"
    assert validate_structured_outlook(outlook, evidence) == []
assert artifact["portfolio_decision"]["approved_actions"] == before_actions
```

Expected: all four artifacts preserve trade decisions; valid live outlooks pass every source/number check, or explicitly degrade unavailable.

- [ ] **Step 4: Force-run watch/pre-close sessions and verify no LLM call/repetition**

Run the four observation sessions with a transport-call counter or log marker. Expected: zero outlook LLM calls; unchanged outlook produces no full outlook block; a seeded changed pair produces one deterministic delta and does not repeat it.

- [ ] **Step 5: Scan actual rendered reports**

Run:

`uv run python scripts/human_readable_scan.py .local/scheduled_runs/latest/`

Render each primary payload through `scripts/run_push_report.py`. Cross-check every visible number and URL against `portfolio_decision.user_view`. Expected: no internal IDs, unauthorized numbers, unsupported sources, or trade instructions inside the outlook.

- [ ] **Step 6: Update architecture and operator documentation**

Document the evidence boundary, schema, confidence caps, primary/watch policy, cache/expiry, failure behavior, external model env vars, and confirmation-only forecast candidates in the three named docs. Do not copy credentials or machine-specific secrets.

- [ ] **Step 7: Manually trigger one primary cron job and read back the saved cron output**

Run `HERMES_HOME=/mnt/user/appdata/hermes /opt/hermes/.venv/bin/hermes cron run 006c84954279` for the A股盘后 primary job. Verify the newest file under `/mnt/user/appdata/hermes/cron/output/006c84954279/` contains trade card first, assistant second, then the outlook. Feishu API delivery receipt is not required if the bot cannot read the group; cron last status and local output are the verifiable boundaries.

- [ ] **Step 8: Independent review and final fixes**

Run a code review focused on source provenance, numeric authority, action-boundary leakage, cache staleness, observation deduplication, and real-data financial reasonableness. Fix every P0/P1 finding, rerun the full suite and real artifact audit.

- [ ] **Step 9: Commit final verification/docs**

```bash
git add AGENT_GUIDE.md DATA_MODEL.md ARCHITECTURE.md
git commit -m "docs: document structured outlook pipeline"
```

- [ ] **Step 10: Final branch audit**

Run:

`git status --short --branch`

`git log --oneline --decorate -12`

`git diff --check HEAD~8..HEAD`

Expected: clean worktree, expected task commits only, no whitespace errors.
