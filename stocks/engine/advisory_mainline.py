"""M2 advisory mainline: wire the LLM Investment Analyst into the push outlook.

This module orchestrates snapshot → synthesis → validation → projection for
primary sessions, producing a ``structured_outlook`` dict whose top-level
shape matches what ``presentation.project_outlook_for_display`` whitelists
and what ``compute_outlook_delta`` / ``build_forecast_candidates`` already
consume.  Every failure path degrades to an honest ``研判待复核``
unavailable outlook — never a fabricated judgment.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from stocks.domain.advisory_models import (
    AdvisoryScenario,
    InvestmentAdvisory,
    UnifiedAnalysisSnapshot,
)
from stocks.domain.models import AnalysisContext
from stocks.engine.advisory_contract import validate_advisory
from stocks.engine.advisory_synthesizer import synthesize_advisory
from stocks.engine.unified_snapshot import build_unified_snapshot
from stocks.providers.openai_client import LLMClient

logger = logging.getLogger(__name__)

# Quote-freshness values that mean "do not let an LLM judge this market".
_STALE_FRESHNESS = frozenset({"stale", "old", "missing", "no_data", "unknown", ""})

# Snapshot older than this relative to `now` is too old to judge.
_MAX_SNAPSHOT_AGE_SECONDS = 90 * 60

# Direction mapping for sector / asset-class views derived from advisory actions.
_DIRECTION_BY_ACTION = {
    "buy": "supportive",
    "add": "supportive",
    "sell": "adverse",
    "reduce": "adverse",
}


def _unavailable(message: str, *, generated_at: str, data_limitations: list[str] | None = None) -> dict:
    """Build the honest unavailable outlook dict."""
    return {
        "status": "unavailable",
        "generated_at": generated_at,
        "message": message,
        "data_limitations": list(data_limitations or [])[:3],
    }


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _load_secret_value(name: str, *, want_url: bool) -> Optional[str]:
    """Bare-value fallback from .secret/*.md (mirrors outlook_synthesizer)."""
    secret_path = Path(".secret") / name
    if not secret_path.exists():
        return None
    lines = [line.strip() for line in secret_path.read_text("utf-8").splitlines() if line.strip()]
    if not lines:
        return None
    if want_url:
        for line in lines:
            if line.startswith("http"):
                return line
        return None
    for line in lines:
        if not line.startswith("http"):
            return line
    return lines[0] if len(lines) == 1 else None


def _resolve_outlook_credentials(config: dict) -> tuple[str, str, int, str, list[str]]:
    """Read ``config["llm"]["outlook"]`` credentials and model chain settings.

    Returns ``(api_key, base_url, timeout, primary_model, fallback_models)``;
    ``api_key``/``base_url`` are empty strings when unconfigured.
    """
    outlook_cfg = (config.get("llm") or {}).get("outlook") or {}
    model = str(outlook_cfg.get("model") or "deepseek-v4-pro")
    api_key_env = str(outlook_cfg.get("api_key_env") or "OPENAI_COMPATIBLE_API_KEY")
    base_url_env = str(outlook_cfg.get("base_url_env") or "OPENAI_COMPATIBLE_BASE_URL")
    timeout = int(outlook_cfg.get("timeout_seconds") or 120)
    fallback_models = [
        str(m) for m in (outlook_cfg.get("fallback_models") or []) if str(m).strip()
    ]

    api_key = os.environ.get(api_key_env, "").strip() or _load_secret_value("openai-key.md", want_url=False)
    base_url = os.environ.get(base_url_env, "").strip() or _load_secret_value("openai-base-url.md", want_url=True)
    return api_key, base_url, timeout, model, fallback_models


def resolve_mainline_llm_client(config: dict) -> Optional[LLMClient]:
    """Resolve an LLMClient from ``config["llm"]["outlook"]``.

    Priority: environment variables (``api_key_env`` / ``base_url_env``),
    then bare-value files under ``.secret/`` (``openai-key.md`` /
    ``openai-base-url.md``).  Returns ``None`` when either the key or the
    endpoint URL is missing — the caller must degrade, never fabricate.
    """
    api_key, base_url, timeout, model, _ = _resolve_outlook_credentials(config)
    if not api_key or not base_url:
        return None
    return LLMClient(model=model, api_key=api_key, base_url=base_url, timeout=timeout)


def resolve_mainline_llm_clients(config: dict) -> list[LLMClient]:
    """Resolve the primary client plus fallback-model chain.

    Fallback models share the primary endpoint/credentials and are tried
    in order when the primary model fails (timeout, transport or parse
    error).  Returns an empty list when unconfigured — the caller must
    degrade, never fabricate.
    """
    api_key, base_url, timeout, model, fallback_models = _resolve_outlook_credentials(config)
    if not api_key or not base_url:
        return []
    clients = [LLMClient(model=model, api_key=api_key, base_url=base_url, timeout=timeout)]
    for fallback in fallback_models:
        if fallback == model:
            continue
        clients.append(LLMClient(model=fallback, api_key=api_key, base_url=base_url, timeout=timeout))
    return clients


def _quotes_freshness_blocked(context: AnalysisContext, market: str) -> bool:
    """True when the primary market's quotes are stale/missing/unknown."""
    data_quality = getattr(context, "data_quality", None) or {}
    quotes = data_quality.get("quotes") if isinstance(data_quality, dict) else None
    by_market = (quotes or {}).get("by_market") or {}
    market_key = {"cn": "a", "us": "us"}.get(market, market)
    entry = by_market.get(market_key)
    if not isinstance(entry, dict):
        return True
    freshness = str(entry.get("freshness") or "").strip().lower()
    return freshness in _STALE_FRESHNESS


def _snapshot_too_old(context: AnalysisContext, now: str) -> bool:
    """True when the context snapshot is older than 90 minutes relative to now."""
    generated = _parse_iso(str(getattr(context, "generated_at", "") or ""))
    current = _parse_iso(now) or datetime.now(timezone.utc)
    if generated is None:
        return True
    return (current - generated).total_seconds() > _MAX_SNAPSHOT_AGE_SECONDS


def _is_fallback_advisory(advisory: InvestmentAdvisory) -> bool:
    """Detect the deterministic hold_default fallback (LLM unavailable/errored)."""
    return any(a.action_id == "hold_default" for a in advisory.hold_decisions)


def _project_horizon(outlook: Any, horizon_label: str) -> dict:
    if outlook is None:
        return {}
    projected = {
        "horizon": horizon_label,
        "direction": outlook.direction,
        "confidence": outlook.confidence,
        "rationale": outlook.rationale,
        "validation": outlook.validation,
        "falsification": outlook.falsification,
    }
    return {k: v for k, v in projected.items() if v}


def _project_scenarios(scenarios: tuple[AdvisoryScenario, ...]) -> dict:
    projected: dict[str, dict] = {}
    for scenario in scenarios:
        if scenario.name not in ("base", "bull", "risk"):
            continue
        entry: dict[str, Any] = {"label": scenario.description or scenario.name}
        if scenario.trigger:
            entry["validation"] = [scenario.trigger]
        if scenario.invalidation:
            entry["invalidation"] = [scenario.invalidation]
        projected[scenario.name] = entry
    return projected


def _project_views(actions: tuple, *, key: str) -> list[dict]:
    views: list[dict] = []
    for action in actions:
        direction = _DIRECTION_BY_ACTION.get(action.action, "neutral")
        view = {key: action.target, "direction": direction, "rationale": action.reasoning}
        views.append({k: v for k, v in view.items() if v})
    return views


def _project_source_refs(advisory: InvestmentAdvisory) -> list[dict]:
    seen: list[str] = []
    for outlook in (advisory.short_term, advisory.medium_term):
        if outlook is None:
            continue
        for ref in outlook.source_refs:
            if ref and ref not in seen:
                seen.append(ref)
    for scenario in advisory.scenarios:
        for ref in scenario.evidence_refs:
            if ref and ref not in seen:
                seen.append(ref)
    return [{"id": ref, "source": ref} for ref in seen[:5]]


def _project_forecast_candidates(advisory: InvestmentAdvisory) -> list[dict]:
    candidates: list[dict] = []
    for forecast in advisory.forecast_candidates:
        try:
            level = float(str(forecast.level).strip())
        except (TypeError, ValueError):
            continue
        candidates.append(
            {
                "statement": forecast.statement,
                "target": forecast.target,
                "metric": forecast.metric,
                "comparator": forecast.comparator,
                "level": level,
                "deadline": forecast.deadline,
                "confidence": forecast.confidence,
                "source_ref_ids": list(forecast.evidence_refs),
                "requires_confirmation": True,
            }
        )
    return candidates


def _project_outlook(advisory: InvestmentAdvisory, receipt: Any, *, generated_at: str) -> dict:
    return {
        "status": "ok",
        "generated_at": generated_at,
        "summary": (advisory.market_assessment or "")[:300],
        "near_term": _project_horizon(advisory.short_term, "3-7天"),
        "medium_term": _project_horizon(advisory.medium_term, "1-3个月"),
        "scenarios": _project_scenarios(advisory.scenarios),
        "source_refs": _project_source_refs(advisory),
        "sector_views": _project_views(advisory.sector_opportunities, key="sector"),
        "asset_views": _project_views(advisory.asset_class_opportunities, key="asset_class"),
        "data_limitations": list(advisory.data_limitations)[:3],
        "forecast_candidates": _project_forecast_candidates(advisory),
        "advisory_receipt": asdict(receipt),
    }


# C1-WP2: 置信度降级顺序 high→medium→low;low 不再降。
_CONFIDENCE_ORDER = {"high": 3, "medium": 2, "low": 1, "unknown": 0}


def _downgrade_confidence(confidence: str) -> str:
    cur = _CONFIDENCE_ORDER.get(str(confidence or "").lower(), 0)
    if cur >= 2:
        return "medium" if cur == 3 else "low"
    return str(confidence or "unknown").lower()


def _apply_freshness_downgrade(
    outlook: dict,
    context: AnalysisContext,
) -> dict:
    """C1-WP2: 关键数据过旧时确定性降级研判置信度(不依赖 LLM)。

    输入 context.data_quality:
    - macro.official.freshness in {old, stale} → 官方统计滞后(8/6 实测 6/1)
    - quotes 任一主市场 freshness in {old, stale} → 行情滞后
    规则:任一触发且该 horizon confidence 为 medium/high → 降一级;
    data_limitations 追加"研判基于 N 天前宏观数据,可信度已降级"。

    不改 rationale/validation 文本 —— 诚实保留 LLM 原话,只调可信度。
    """
    dq = (context.data_quality or {}) if isinstance(context.data_quality, dict) else {}
    macro = dq.get("macro") or {}
    official = macro.get("official") or {}
    official_fresh = str(official.get("freshness") or "")
    official_as_of = str(official.get("as_of") or "")[:10]
    quotes = dq.get("quotes") or {}
    markets = (quotes.get("by_market") or {}) if isinstance(quotes, dict) else {}
    market_fresh = [
        str((m or {}).get("freshness") or "")
        for m in markets.values()
        if isinstance(m, dict)
    ]
    stale_official = official_fresh in {"old", "stale"}
    stale_quotes = any(f in {"old", "stale"} for f in market_fresh)
    if not (stale_official or stale_quotes):
        return outlook

    reasons: list[str] = []
    if stale_official:
        reasons.append(f"宏观官方统计滞后(截止 {official_as_of or '未知'})")
    if stale_quotes:
        reasons.append("行情数据滞后")

    out = dict(outlook)
    downgraded = False
    for key in ("near_term", "medium_term"):
        horizon = dict(out.get(key) or {})
        if not horizon:
            continue
        new_conf = _downgrade_confidence(str(horizon.get("confidence") or ""))
        if new_conf != str(horizon.get("confidence") or ""):
            downgraded = True
        horizon["confidence"] = new_conf
        out[key] = horizon

    if downgraded:
        limits = list(out.get("data_limitations") or [])
        note = "研判基于" + "、".join(reasons) + "的数据，可信度已自动降级"
        if note not in limits:
            limits = limits[:2] + [note]
        out["data_limitations"] = limits
    return out


def build_advisory_outlook(
    context: AnalysisContext,
    *,
    session_id: str,
    market: str,
    config: Optional[dict] = None,
    llm_client: Any = "auto",
    now: str = "",
) -> dict:
    """Build the production structured_outlook for a primary session.

    Returns a dict with ``status == "ok"`` plus the whitelisted outlook
    fields, or an ``unavailable`` dict with a sanitized Chinese 研判待复核
    message.  Never raises for expected failure modes; never fabricates.
    """
    generated_at = now or datetime.now(timezone.utc).isoformat()

    # 1. Freshness gate: stale/missing primary-market quotes or an old
    #    context snapshot mean no judgment may be issued.
    if _quotes_freshness_blocked(context, market):
        logger.info("advisory mainline: quotes freshness gate blocked (%s)", market)
        # P5-4: 给用户可行动的恢复提示(行情恢复后下个检查点自动重试)
        return _unavailable(
            "研判待复核：目标市场行情数据过旧或缺失（行情恢复后自动重试）",
            generated_at=generated_at,
        )
    if _snapshot_too_old(context, now):
        logger.info("advisory mainline: context snapshot older than 90 minutes")
        return _unavailable(
            "研判待复核：数据快照过旧（下次定时窗口自动刷新）",
            generated_at=generated_at,
        )

    # 2. LLM client resolution.  "auto" resolves the primary client plus the
    #    configured fallback-model chain from config; an explicit client
    #    (tests, ad-hoc runs) is used as-is; None disables the path.
    if llm_client == "auto":
        clients = resolve_mainline_llm_clients(config or {})
    elif llm_client is None:
        clients = []
    else:
        clients = [llm_client]
    if not clients:
        logger.info("advisory mainline: no LLM client configured")
        return _unavailable("研判待复核：LLM 分析端未配置", generated_at=generated_at)

    # 3. Snapshot → synthesis with per-client retries, then next model in
    #    the chain.  Only transport/parse failures (deterministic fallback
    #    advisory) trigger another attempt; a validated advisory is final.
    outlook_cfg = (config or {}).get("llm", {}).get("outlook", {}) if isinstance(config, dict) else {}
    retry_attempts = max(0, int(outlook_cfg.get("retry_attempts") or 0))
    snapshot: UnifiedAnalysisSnapshot = build_unified_snapshot(
        context, trigger="scheduled", session=session_id, market_scope=market,
    )
    advisory = None
    last_limitations: list[str] = []
    for index, client in enumerate(clients):
        model_label = getattr(client, "model", f"client#{index}")
        for attempt in range(1 + retry_attempts):
            advisory = synthesize_advisory(snapshot, llm_client=client)
            if not _is_fallback_advisory(advisory):
                break
            last_limitations = list(advisory.data_limitations)
            logger.info(
                "advisory mainline: synthesis fallback (model=%s attempt=%d/%d)",
                model_label, attempt + 1, 1 + retry_attempts,
            )
        if advisory is not None and not _is_fallback_advisory(advisory):
            if index > 0:
                logger.info("advisory mainline: succeeded with fallback model %s", model_label)
            break
    if advisory is None or _is_fallback_advisory(advisory):
        logger.info("advisory mainline: LLM synthesis fell back to hold_default")
        return _unavailable(
            "研判待复核：LLM 分析暂不可用，下期重试",
            generated_at=generated_at,
            data_limitations=last_limitations,
        )

    # 4. Contract validation: receipt errors mean the judgment is unusable.
    receipt = validate_advisory(advisory, snapshot_hash=snapshot.snapshot_id)
    if receipt.errors:
        logger.info("advisory mainline: validation errors: %s", list(receipt.errors))
        return _unavailable(
            "研判待复核：本期研判未通过校验",
            generated_at=generated_at,
            data_limitations=list(advisory.data_limitations),
        )

    # 5. Project into the display-shaped outlook dict.
    projected = _project_outlook(advisory, receipt, generated_at=generated_at)
    # C1-WP2: 关键数据过旧时确定性降级置信度(不改 rationale 文本)。
    return _apply_freshness_downgrade(projected, context)
