#!/usr/bin/env python3
"""Deterministic quality audit over historical scheduled-run report artifacts.

Reproduces the concrete production report defects this pipeline must not
regress on: ratio/text drift between the decision layer and the rendered
instruction card, unsettled sale proceeds treated as already-available cash,
non-canonical settlement/risk vocabulary leaking into the user-facing brief,
outlook narrative claims with no source_refs, actions built from stale
cross-market quotes, and the same instrument appearing as both an executable
action and a research-only candidate. Read-only: never mutates artifacts.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterator

_CANONICAL_RISK_LABELS = frozenset({"对冲/高风险", "降风险", "观察", "常态"})
_SETTLEMENT_TOKEN_RE = re.compile(r"^T\+\d$")
_AFTER_PROCEEDS_RE = re.compile(r"^after_T\+\d_proceeds$")
_REDUCE_SIGNALS = frozenset({"reduce", "stop_loss", "take_profit"})
_PCT_RE = re.compile(r"(减仓|加仓|止盈|止损)\s*(\d+(?:\.\d+)?)\s*%")
_PRIMARY_OUTLOOK_SESSIONS = frozenset(
    {"cn_after_close", "us_after_close", "cn_post_open", "us_post_open"}
)


@dataclass
class Finding:
    severity: str  # "P0" (blocking) | "P1" (informational, non-blocking)
    check: str
    session: str
    market: str
    market_date: str
    run_id: str
    message: str
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "check": self.check,
            "session": self.session,
            "market": self.market,
            "market_date": self.market_date,
            "run_id": self.run_id,
            "message": self.message,
            "detail": self.detail,
        }


def _meta(run: dict) -> dict:
    return {
        "session": str(run.get("session") or ""),
        "market": str(run.get("market") or ""),
        "market_date": str(run.get("market_date") or ""),
        "run_id": str(run.get("run_id") or ""),
    }


def _market_prefix(position_id: str) -> str | None:
    if position_id.startswith("a_"):
        return "a"
    if position_id.startswith("us_"):
        return "us"
    return None


# ---------------------------------------------------------------------------
# Checks. Each takes a parsed scheduled-run artifact dict and returns findings.
# ---------------------------------------------------------------------------


def check_final_ratio_text_consistency(run: dict) -> list[Finding]:
    """The rendered instruction-card ratio must match the percentage stated
    in its own reason text (e.g. ratio=0.25 rendered next to "减仓 50%")."""
    meta = _meta(run)
    findings = []
    actions = (
        ((run.get("portfolio_decision") or {}).get("user_view") or {}).get(
            "instruction_card"
        )
        or {}
    ).get("actions") or []
    for action in actions:
        ratio = action.get("ratio")
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
            continue
        text = " ".join(str(action.get(k) or "") for k in ("reason_summary", "action_label"))
        m = _PCT_RE.search(text)
        if not m:
            continue
        stated_pct = float(m.group(2))
        actual_pct = abs(float(ratio)) * 100
        if abs(stated_pct - actual_pct) > 1.0:
            findings.append(
                Finding(
                    severity="P0",
                    check="final_ratio_text_consistency",
                    **meta,
                    message=(
                        f"instruction_card action '{action.get('display_label')}' has "
                        f"ratio={ratio} ({actual_pct:.1f}%) but its own text states "
                        f"{stated_pct:.1f}%"
                    ),
                    detail={"display_label": action.get("display_label"), "ratio": ratio, "text": text},
                )
            )
    return findings


def check_planned_sale_vs_settling(run: dict) -> list[Finding]:
    """A planned sale that settles later than T+0 must be classified as
    settling cash, and must not simultaneously be counted as available now."""
    meta = _meta(run)
    findings = []
    decision = run.get("portfolio_decision") or {}
    cash = decision.get("cash_schedule") or {}
    immediate_ids = set(cash.get("immediate_cash_position_ids") or [])
    settling_ids = set(cash.get("settling_cash_position_ids") or [])
    for action in decision.get("approved_actions") or []:
        signal = str(action.get("signal") or "")
        timing = str(action.get("settlement_timing") or "")
        pid = str(action.get("position_id") or "")
        if signal not in _REDUCE_SIGNALS or timing in ("", "T+0") or timing.startswith("after_"):
            continue
        if pid and pid not in settling_ids:
            findings.append(
                Finding(
                    severity="P0",
                    check="planned_sale_vs_settling",
                    **meta,
                    message=(
                        f"approved {signal} on {pid} settles {timing} but {pid} is absent "
                        "from cash_schedule.settling_cash_position_ids"
                    ),
                    detail={"position_id": pid, "settlement_timing": timing},
                )
            )
        if pid and pid in immediate_ids:
            findings.append(
                Finding(
                    severity="P0",
                    check="planned_sale_vs_settling",
                    **meta,
                    message=(
                        f"{pid} is counted in immediate_cash_position_ids while its "
                        f"{signal} settles {timing} and is not yet available"
                    ),
                    detail={"position_id": pid, "settlement_timing": timing},
                )
            )
    return findings


def check_settlement_rule_vocabulary(run: dict) -> list[Finding]:
    """settlement_timing must use canonical tokens, not free text."""
    meta = _meta(run)
    findings = []
    decision = run.get("portfolio_decision") or {}
    for action in decision.get("approved_actions") or []:
        timing = action.get("settlement_timing")
        if timing is None:
            continue
        timing = str(timing)
        if _SETTLEMENT_TOKEN_RE.match(timing) or _AFTER_PROCEEDS_RE.match(timing):
            continue
        findings.append(
            Finding(
                severity="P0",
                check="settlement_rule_vocabulary",
                **meta,
                message=(
                    f"approved action on {action.get('position_id')} has non-canonical "
                    f"settlement_timing '{timing}'"
                ),
                detail={"position_id": action.get("position_id"), "settlement_timing": timing},
            )
        )
    return findings


def check_exact_risk_labels(run: dict) -> list[Finding]:
    """assistant_brief.risk.label must be one of the four canonical strings."""
    meta = _meta(run)
    risk = (
        ((run.get("portfolio_decision") or {}).get("user_view") or {}).get("assistant_brief")
        or {}
    ).get("risk") or {}
    label = risk.get("label")
    if label is None or label in _CANONICAL_RISK_LABELS:
        return []
    return [
        Finding(
            severity="P0",
            check="exact_risk_labels",
            **meta,
            message=(
                f"risk.label '{label}' is not one of the canonical labels "
                f"{sorted(_CANONICAL_RISK_LABELS)}"
            ),
            detail={"label": label},
        )
    ]


def check_padding_only_intelligence(run: dict) -> list[Finding]:
    """Padding coverage is metadata, never directional evidence: directional
    counts must not exceed the non-padding slice of covered items."""
    meta = _meta(run)
    coverage = run.get("intelligence_coverage") or {}
    field_total, padding, directional = (
        coverage.get("field"),
        coverage.get("padding"),
        coverage.get("directional"),
    )
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (field_total, padding, directional)):
        return []
    if directional > (field_total - padding):
        return [
            Finding(
                severity="P0",
                check="padding_only_intelligence",
                **meta,
                message=(
                    f"directional={directional} exceeds non-padding coverage "
                    f"(field={field_total} - padding={padding}); padding-only items "
                    "appear to be counted as directional evidence"
                ),
                detail=coverage,
            )
        ]
    return []


def check_missing_source_refs(run: dict) -> list[Finding]:
    """A structured outlook that makes narrative claims must cite source_refs."""
    meta = _meta(run)
    outlook = run.get("structured_outlook") or {}
    if outlook.get("status") != "ok":
        return []
    has_claims = bool(outlook.get("summary")) or bool(outlook.get("asset_views")) or bool(
        outlook.get("sector_views")
    )
    if not has_claims:
        return []
    if outlook.get("source_refs"):
        return []
    return [
        Finding(
            severity="P0",
            check="missing_source_refs",
            **meta,
            message=(
                "structured_outlook has narrative claims (summary/asset_views/sector_views) "
                "but an empty source_refs list"
            ),
            detail={"summary": outlook.get("summary")},
        )
    ]


def check_cross_market_stale_actions(run: dict) -> list[Finding]:
    """An action on a position from a market other than the session's own
    market must not be built from stale/old quotes for that market."""
    meta = _meta(run)
    findings = []
    run_market = str(run.get("market") or "")
    by_market = (
        ((run.get("data_boundaries") or {}).get("data_quality") or {}).get("quotes") or {}
    ).get("by_market") or {}
    decision = run.get("portfolio_decision") or {}
    for action in decision.get("approved_actions") or []:
        pid = str(action.get("position_id") or "")
        prefix = _market_prefix(pid)
        if not prefix or prefix == run_market:
            continue
        freshness = str((by_market.get(prefix) or {}).get("freshness") or "")
        if freshness in ("stale", "old"):
            findings.append(
                Finding(
                    severity="P0",
                    check="cross_market_stale_actions",
                    **meta,
                    message=(
                        f"{run_market} session approved an action on {prefix}-market "
                        f"position {pid} using {freshness} {prefix} quotes"
                    ),
                    detail={"position_id": pid, "freshness": freshness},
                )
            )
    return findings


def check_action_research_duplication(run: dict) -> list[Finding]:
    """The same instrument must not appear as both an executable action and a
    research-only candidate in the same run."""
    meta = _meta(run)
    findings = []
    reviews_by_id = {
        str(r.get("position_id") or ""): r for r in (run.get("position_reviews") or [])
    }
    decision = run.get("portfolio_decision") or {}
    action_keys = set()
    for action in decision.get("approved_actions") or []:
        pid = str(action.get("position_id") or "")
        key = str((reviews_by_id.get(pid) or {}).get("instrument_key") or "")
        if key:
            action_keys.add(key)
    for candidate in run.get("research_candidates") or []:
        symbol = str(candidate.get("symbol") or "")
        if symbol and symbol in action_keys:
            findings.append(
                Finding(
                    severity="P0",
                    check="action_research_duplication",
                    **meta,
                    message=(
                        f"{symbol} appears both as an approved executable action and a "
                        "research-only candidate in the same run"
                    ),
                    detail={"symbol": symbol},
                )
            )
    return findings


def check_advisory_receipt_coverage(run: dict, shadow_root: Path) -> list[Finding]:
    """Primary outlook sessions should have a same-day advisory shadow trial
    with a non-degraded receipt. Informational only: the continuous shadow
    trial cadence is a later-stage acceptance gate, not a Task 0-2 gate."""
    meta = _meta(run)
    session = meta["session"]
    if session not in _PRIMARY_OUTLOOK_SESSIONS:
        return []
    generated_day = str(run.get("generated_at") or "")[:10].replace("-", "")
    if not generated_day:
        return []
    if not shadow_root.exists():
        return [
            Finding(
                severity="P1",
                check="advisory_receipt_coverage",
                **meta,
                message="advisory shadow store directory does not exist; no receipt coverage",
                detail={},
            )
        ]
    matches = [
        p
        for p in shadow_root.iterdir()
        if p.is_dir() and p.name.startswith(f"{session}-{generated_day}")
    ]
    if not matches:
        return [
            Finding(
                severity="P1",
                check="advisory_receipt_coverage",
                **meta,
                message=f"no advisory shadow trial found for session={session} on {generated_day}",
                detail={},
            )
        ]
    for trial_dir in matches:
        receipt_path = trial_dir / "receipt.json"
        if not receipt_path.exists():
            continue
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if receipt.get("status") in ("ok", "warnings"):
            return []
    return [
        Finding(
            severity="P1",
            check="advisory_receipt_coverage",
            **meta,
            message=(
                f"advisory shadow trial(s) found for {session} on {generated_day} but none "
                "has an ok/warnings receipt"
            ),
            detail={"trials": [p.name for p in matches]},
        )
    ]


_ARTIFACT_CHECKS = (
    check_final_ratio_text_consistency,
    check_planned_sale_vs_settling,
    check_settlement_rule_vocabulary,
    check_exact_risk_labels,
    check_padding_only_intelligence,
    check_missing_source_refs,
    check_cross_market_stale_actions,
    check_action_research_duplication,
)


def run_all_checks(run: dict, shadow_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for check in _ARTIFACT_CHECKS:
        findings.extend(check(run))
    findings.extend(check_advisory_receipt_coverage(run, shadow_root))
    return findings


def iter_artifacts(history_root: Path, start: date, end: date) -> Iterator[Path]:
    """Yield dated scheduled-run artifact paths under history_root within [start, end]."""
    if not history_root.exists():
        return
    for day_dir in sorted(history_root.iterdir()):
        if not day_dir.is_dir():
            continue
        try:
            day = date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        if day < start or day > end:
            continue
        for market_dir in sorted(p for p in day_dir.iterdir() if p.is_dir()):
            for session_dir in sorted(p for p in market_dir.iterdir() if p.is_dir()):
                yield from sorted(session_dir.glob("*.json"))


def audit_history(
    history_root: Path, shadow_root: Path, start: date, end: date
) -> tuple[list[Finding], list[str]]:
    """Return (findings, unreadable_paths) across all artifacts in [start, end]."""
    findings: list[Finding] = []
    unreadable: list[str] = []
    for path in iter_artifacts(history_root, start, end):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            unreadable.append(f"{path}: {exc}")
            continue
        findings.extend(run_all_checks(run, shadow_root))
    return findings, unreadable


def _summarize(findings: list[Finding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.check] = counts.get(f.check, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument(
        "--history-root", default=".local/scheduled_runs", help="dated scheduled-run history root"
    )
    parser.add_argument(
        "--shadow-root", default=".local/advisory_shadow", help="advisory shadow trial root"
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON to stdout")
    args = parser.parse_args(argv)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    history_root = Path(args.history_root)
    shadow_root = Path(args.shadow_root)

    findings, unreadable = audit_history(history_root, shadow_root, start, end)
    p0 = [f for f in findings if f.severity == "P0"]
    p1 = [f for f in findings if f.severity == "P1"]

    if args.json:
        print(
            json.dumps(
                {
                    "start": args.start,
                    "end": args.end,
                    "p0_count": len(p0),
                    "p1_count": len(p1),
                    "unreadable": unreadable,
                    "findings": [f.to_dict() for f in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"Audit window: {args.start}..{args.end}")
        print(f"P0 findings: {len(p0)}  P1 findings: {len(p1)}")
        if unreadable:
            print(f"Unreadable artifacts: {len(unreadable)}")
            for u in unreadable:
                print(f"  - {u}")
        for f in findings:
            print(f"[{f.severity}] {f.check} {f.session}/{f.market_date} ({f.run_id}): {f.message}")
        if not findings:
            print("No findings.")
        counts = _summarize(findings)
        if counts:
            print("By check:")
            for check, count in sorted(counts.items()):
                print(f"  {check}: {count}")

    return 1 if p0 else 0


if __name__ == "__main__":
    sys.exit(main())
