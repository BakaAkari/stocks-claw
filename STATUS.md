# STATUS.md

The **only** source of current dynamic project state. `ROADMAP.md`,
`ARCHITECTURE.md`, and `stocks/DATA_MODEL.md` describe direction and shape;
none of them record phase/completion status — that lives here only.

> Update this file once per task, right after that task's implementation is
> stable and its focused tests pass. Overwrite the stale sections below;
> don't append a history log here (decision history belongs in `PLAN.md`).
>
> Last updated: 2026-08-03 (seventh update) — institution_type 链路修复 +
> 报告可用性评审 + P0 修复（LLM 重试/备用模型链、风险状态路径锚定）。
> W1 watchlist is next under M5.
> Earlier updates: M3 (`83e94ec`), A1 (`c313d22`), M2 (`7c35c7f`, live-LLM
> verified), M1 (`382207b`).

## 2026-08-03 三连修复（评审驱动）

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
