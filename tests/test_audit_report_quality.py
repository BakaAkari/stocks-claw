from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.audit_report_quality import (
    audit_history,
    check_action_research_duplication,
    check_advisory_receipt_coverage,
    check_cross_market_stale_actions,
    check_exact_risk_labels,
    check_final_ratio_text_consistency,
    check_missing_source_refs,
    check_padding_only_intelligence,
    check_planned_sale_vs_settling,
    check_settlement_rule_vocabulary,
    iter_artifacts,
    main,
)

SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_report_quality.py"


def _base_run(**overrides):
    run = {
        "session": "cn_after_close",
        "market": "cn",
        "market_date": "2026-07-28",
        "run_id": "20260728T070000Z_cn_after_close",
        "generated_at": "2026-07-28T07:00:00+00:00",
        "portfolio_decision": {
            "approved_actions": [],
            "cash_schedule": {},
            "user_view": {"instruction_card": {"actions": []}, "assistant_brief": {}},
        },
        "position_reviews": [],
        "research_candidates": [],
        "intelligence_coverage": {},
        "structured_outlook": {},
        "data_boundaries": {"data_quality": {"quotes": {"by_market": {}}}},
    }
    run.update(overrides)
    return run


def test_final_ratio_text_consistency_flags_mismatch():
    run = _base_run()
    run["portfolio_decision"]["user_view"]["instruction_card"]["actions"] = [
        {"display_label": "沪深300ETF（510300）", "ratio": 0.25, "reason_summary": "趋势走弱，减仓 50%", "action_label": "减仓"},
    ]
    findings = check_final_ratio_text_consistency(run)
    assert len(findings) == 1
    assert findings[0].severity == "P0"
    assert "50.0%" in findings[0].message and "25.0%" in findings[0].message


def test_final_ratio_text_consistency_allows_matching_text():
    run = _base_run()
    run["portfolio_decision"]["user_view"]["instruction_card"]["actions"] = [
        {"display_label": "科创50ETF（588000）", "ratio": 0.5, "reason_summary": "趋势走弱，减仓 50%", "action_label": "减仓"},
    ]
    assert check_final_ratio_text_consistency(run) == []


def test_planned_sale_vs_settling_flags_missing_settling_bucket():
    run = _base_run()
    run["portfolio_decision"]["approved_actions"] = [
        {"position_id": "a_510300", "signal": "reduce", "settlement_timing": "T+1"},
    ]
    run["portfolio_decision"]["cash_schedule"] = {
        "settling_cash_position_ids": [],
        "immediate_cash_position_ids": [],
    }
    findings = check_planned_sale_vs_settling(run)
    assert len(findings) == 1
    assert "absent from cash_schedule.settling_cash_position_ids" in findings[0].message


def test_planned_sale_vs_settling_flags_double_counted_as_immediate():
    run = _base_run()
    run["portfolio_decision"]["approved_actions"] = [
        {"position_id": "a_510300", "signal": "reduce", "settlement_timing": "T+1"},
    ]
    run["portfolio_decision"]["cash_schedule"] = {
        "settling_cash_position_ids": ["a_510300"],
        "immediate_cash_position_ids": ["a_510300"],
    }
    findings = check_planned_sale_vs_settling(run)
    assert any("not yet available" in f.message for f in findings)


def test_planned_sale_vs_settling_allows_correctly_classified_sale():
    run = _base_run()
    run["portfolio_decision"]["approved_actions"] = [
        {"position_id": "a_510300", "signal": "reduce", "settlement_timing": "T+1"},
    ]
    run["portfolio_decision"]["cash_schedule"] = {
        "settling_cash_position_ids": ["a_510300"],
        "immediate_cash_position_ids": [],
    }
    assert check_planned_sale_vs_settling(run) == []


def test_planned_sale_vs_settling_ignores_t0_and_replacement_legs():
    run = _base_run()
    run["portfolio_decision"]["approved_actions"] = [
        {"position_id": "a_cash_leg", "signal": "reduce", "settlement_timing": "T+0"},
        {"position_id": "a_buy_leg", "signal": "add", "settlement_timing": "after_T+1_proceeds"},
    ]
    assert check_planned_sale_vs_settling(run) == []


def test_settlement_rule_vocabulary_rejects_free_text():
    run = _base_run()
    run["portfolio_decision"]["approved_actions"] = [
        {"position_id": "a_510300", "settlement_timing": "到账时间待确认"},
    ]
    findings = check_settlement_rule_vocabulary(run)
    assert len(findings) == 1
    assert "non-canonical" in findings[0].message


def test_settlement_rule_vocabulary_allows_canonical_tokens():
    run = _base_run()
    run["portfolio_decision"]["approved_actions"] = [
        {"position_id": "a_1", "settlement_timing": "T+0"},
        {"position_id": "a_2", "settlement_timing": "T+2"},
        {"position_id": "a_3", "settlement_timing": "after_T+1_proceeds"},
    ]
    assert check_settlement_rule_vocabulary(run) == []


def test_exact_risk_labels_rejects_non_canonical_label():
    run = _base_run()
    run["portfolio_decision"]["user_view"]["assistant_brief"] = {"risk": {"label": "防御状态"}}
    findings = check_exact_risk_labels(run)
    assert len(findings) == 1
    assert "防御状态" in findings[0].message


def test_exact_risk_labels_allows_canonical_labels():
    for label in ("对冲/高风险", "降风险", "观察", "常态"):
        run = _base_run()
        run["portfolio_decision"]["user_view"]["assistant_brief"] = {"risk": {"label": label}}
        assert check_exact_risk_labels(run) == [], label


def test_padding_only_intelligence_flags_directional_overlap_with_padding():
    run = _base_run()
    run["intelligence_coverage"] = {"field": 64, "padding": 64, "directional": 5}
    findings = check_padding_only_intelligence(run)
    assert len(findings) == 1


def test_padding_only_intelligence_allows_directional_within_non_padding_slice():
    run = _base_run()
    run["intelligence_coverage"] = {"field": 64, "padding": 64, "directional": 0}
    assert check_padding_only_intelligence(run) == []


def test_missing_source_refs_flags_claims_without_refs():
    run = _base_run()
    run["structured_outlook"] = {"status": "ok", "summary": "市场维持震荡", "source_refs": []}
    findings = check_missing_source_refs(run)
    assert len(findings) == 1


def test_missing_source_refs_allows_claims_with_refs():
    run = _base_run()
    run["structured_outlook"] = {
        "status": "ok", "summary": "市场维持震荡", "source_refs": [{"id": "n1"}],
    }
    assert check_missing_source_refs(run) == []


def test_missing_source_refs_ignores_unavailable_outlook():
    run = _base_run()
    run["structured_outlook"] = {"status": "unavailable", "summary": "", "source_refs": []}
    assert check_missing_source_refs(run) == []


def test_cross_market_stale_actions_flags_stale_cross_market_position():
    run = _base_run(market="us")
    run["portfolio_decision"]["approved_actions"] = [{"position_id": "a_510300"}]
    run["data_boundaries"] = {
        "data_quality": {"quotes": {"by_market": {"a": {"freshness": "stale"}}}}
    }
    findings = check_cross_market_stale_actions(run)
    assert len(findings) == 1
    assert "a_510300" in findings[0].message


def test_cross_market_stale_actions_allows_fresh_cross_market_position():
    run = _base_run(market="us")
    run["portfolio_decision"]["approved_actions"] = [{"position_id": "a_510300"}]
    run["data_boundaries"] = {
        "data_quality": {"quotes": {"by_market": {"a": {"freshness": "fresh"}}}}
    }
    assert check_cross_market_stale_actions(run) == []


def test_cross_market_stale_actions_ignores_same_market_position():
    run = _base_run(market="a")
    run["portfolio_decision"]["approved_actions"] = [{"position_id": "a_510300"}]
    run["data_boundaries"] = {
        "data_quality": {"quotes": {"by_market": {"a": {"freshness": "stale"}}}}
    }
    assert check_cross_market_stale_actions(run) == []


def test_action_research_duplication_flags_same_instrument():
    run = _base_run()
    run["position_reviews"] = [{"position_id": "a_588000", "instrument_key": "a:588000"}]
    run["portfolio_decision"]["approved_actions"] = [{"position_id": "a_588000"}]
    run["research_candidates"] = [{"symbol": "a:588000"}]
    findings = check_action_research_duplication(run)
    assert len(findings) == 1
    assert "a:588000" in findings[0].message


def test_action_research_duplication_allows_disjoint_sets():
    run = _base_run()
    run["position_reviews"] = [{"position_id": "a_588000", "instrument_key": "a:588000"}]
    run["portfolio_decision"]["approved_actions"] = [{"position_id": "a_588000"}]
    run["research_candidates"] = [{"symbol": "a:159695"}]
    assert check_action_research_duplication(run) == []


def test_advisory_receipt_coverage_reports_missing_trial_as_p1(tmp_path):
    run = _base_run()
    findings = check_advisory_receipt_coverage(run, tmp_path / "advisory_shadow")
    assert len(findings) == 1
    assert findings[0].severity == "P1"


def test_advisory_receipt_coverage_ok_when_receipt_present(tmp_path):
    run = _base_run()
    shadow_root = tmp_path / "advisory_shadow"
    trial_dir = shadow_root / "cn_after_close-20260728T070500"
    trial_dir.mkdir(parents=True)
    (trial_dir / "receipt.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    assert check_advisory_receipt_coverage(run, shadow_root) == []


def test_advisory_receipt_coverage_skips_non_primary_sessions(tmp_path):
    run = _base_run(session="cn_pre_open")
    assert check_advisory_receipt_coverage(run, tmp_path / "advisory_shadow") == []


def test_iter_artifacts_filters_by_date_range(tmp_path):
    history = tmp_path / "scheduled_runs"
    for d in ("2026-07-22", "2026-07-23", "2026-07-29", "2026-07-30"):
        session_dir = history / d / "cn" / "cn_after_close"
        session_dir.mkdir(parents=True)
        (session_dir / "run.json").write_text("{}", encoding="utf-8")
    paths = list(iter_artifacts(history, date_from_iso("2026-07-23"), date_from_iso("2026-07-29")))
    dates = {p.parents[2].name for p in paths}
    assert dates == {"2026-07-23", "2026-07-29"}


def date_from_iso(value: str):
    from datetime import date
    return date.fromisoformat(value)


def test_audit_history_aggregates_findings_across_artifacts(tmp_path):
    history = tmp_path / "scheduled_runs"
    session_dir = history / "2026-07-28" / "cn" / "cn_after_close"
    session_dir.mkdir(parents=True)
    run = _base_run()
    run["portfolio_decision"]["user_view"]["assistant_brief"] = {"risk": {"label": "防御状态"}}
    (session_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    findings, unreadable = audit_history(
        history, tmp_path / "advisory_shadow", date_from_iso("2026-07-23"), date_from_iso("2026-07-29")
    )
    assert unreadable == []
    assert any(f.check == "exact_risk_labels" for f in findings)


def test_audit_history_reports_unreadable_artifacts_without_crashing(tmp_path):
    history = tmp_path / "scheduled_runs"
    session_dir = history / "2026-07-28" / "cn" / "cn_after_close"
    session_dir.mkdir(parents=True)
    (session_dir / "broken.json").write_text("{not json", encoding="utf-8")
    findings, unreadable = audit_history(
        history, tmp_path / "advisory_shadow", date_from_iso("2026-07-23"), date_from_iso("2026-07-29")
    )
    assert findings == []
    assert len(unreadable) == 1


def test_main_exits_nonzero_when_p0_findings_present(tmp_path):
    history = tmp_path / "scheduled_runs"
    session_dir = history / "2026-07-28" / "cn" / "cn_after_close"
    session_dir.mkdir(parents=True)
    run = _base_run()
    run["portfolio_decision"]["user_view"]["assistant_brief"] = {"risk": {"label": "防御状态"}}
    (session_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    exit_code = main([
        "--start", "2026-07-23", "--end", "2026-07-29",
        "--history-root", str(history),
        "--shadow-root", str(tmp_path / "advisory_shadow"),
    ])
    assert exit_code == 1


def test_main_exits_zero_when_only_p1_findings_present(tmp_path):
    history = tmp_path / "scheduled_runs"
    session_dir = history / "2026-07-28" / "cn" / "cn_after_close"
    session_dir.mkdir(parents=True)
    run = _base_run()
    (session_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    exit_code = main([
        "--start", "2026-07-23", "--end", "2026-07-29",
        "--history-root", str(history),
        "--shadow-root", str(tmp_path / "advisory_shadow"),
    ])
    assert exit_code == 0


def test_cli_json_output_is_valid_json(tmp_path):
    history = tmp_path / "scheduled_runs"
    session_dir = history / "2026-07-28" / "cn" / "cn_after_close"
    session_dir.mkdir(parents=True)
    run = _base_run()
    (session_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--start", "2026-07-23", "--end", "2026-07-29",
            "--history-root", str(history),
            "--shadow-root", str(tmp_path / "advisory_shadow"),
            "--json",
        ],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["p0_count"] == 0
