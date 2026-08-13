"""Tests for the M3 feedback loop: model, ledger writes, rollup, snapshot 回流."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from stocks.domain.models import AdviceRecord
from stocks.engine import StocksEngine
from stocks.engine.advice_feedback import (
    compute_feedback_rollup,
    make_feedback,
    summarize_record_for_snapshot,
)
from stocks.engine.unified_snapshot import build_unified_snapshot
from tests.engine.test_engine import MINIMAL_CONFIG
from tests.engine.test_unified_snapshot import _minimal_context

NOW = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)


def _recent(days_ago: int) -> str:
    """相对实时 now 的记录时间,避免硬编码过去时间落在 7 天窗口外。"""
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _record(created_at: str, **overrides) -> dict:
    record = {
        "created_at": created_at,
        "instruments": [{"market": "a", "code": "510300", "name": "沪深300ETF"}],
        "direction": {"a:510300": "hold"},
        "rationale_summary": "测试建议",
        "based_on": ["quotes"],
        "boundary": [{"type": "fact", "text": "测试边界"}],
        "triggers": [],
        "actions": [],
        "feedback": None,
    }
    record.update(overrides)
    return record


class TestAdviceRecordFeedback:
    def test_feedback_round_trip(self) -> None:
        feedback = make_feedback("accepted", "执行了")
        record = AdviceRecord.from_dict(_record("2026-08-01T00:00:00+00:00", feedback=feedback))
        assert record.feedback["status"] == "accepted"
        assert record.to_dict()["feedback"]["note"] == "执行了"

    def test_absent_feedback_is_unmarked(self) -> None:
        record = AdviceRecord.from_dict(_record("2026-08-01T00:00:00+00:00"))
        assert record.feedback is None
        assert record.to_dict()["feedback"] is None

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValueError, match="feedback.status"):
            AdviceRecord.from_dict(
                _record("2026-08-01T00:00:00+00:00",
                        feedback={"status": "loved_it", "note": "", "marked_at": "x"})
            )

    def test_make_feedback_vocabulary(self) -> None:
        with pytest.raises(ValueError, match="feedback status"):
            make_feedback("maybe")


class TestRollup:
    def test_counts_rates_and_window(self) -> None:
        records = [
            _record("2026-07-31T10:00:00+00:00",
                    feedback=make_feedback("accepted", "好")),
            _record("2026-07-30T10:00:00+00:00",
                    feedback=make_feedback("partial", "只执行了一半")),
            _record("2026-07-29T10:00:00+00:00",
                    feedback=make_feedback("rejected", "信息不足，放弃")),
            _record("2026-07-28T10:00:00+00:00"),  # unmarked
            _record("2026-07-01T10:00:00+00:00",
                    feedback=make_feedback("accepted", "窗口外")),  # out of window
        ]
        rollup = compute_feedback_rollup(records, window_days=7, now=NOW)

        assert rollup["total_in_window"] == 4
        assert rollup["by_status"] == {
            "accepted": 1, "deferred": 0, "partial": 1, "rejected": 1,
        }
        assert rollup["unmarked"] == 1
        assert rollup["marked_total"] == 3
        assert rollup["acceptance_rate"] == 0.5  # (1 + 0.5) / 3
        notes = rollup["recent_rejection_notes"]
        assert [n["note"] for n in notes] == ["只执行了一半", "信息不足，放弃"]
        assert rollup["oldest_unmarked"] == "2026-07-28T10:00:00+00:00"

    def test_empty_ledger_zero_state(self) -> None:
        rollup = compute_feedback_rollup([], window_days=7, now=NOW)
        assert rollup["total_in_window"] == 0
        assert rollup["marked_total"] == 0
        assert rollup["acceptance_rate"] is None
        assert rollup["recent_rejection_notes"] == []

    def test_summarize_unmarked_surfaces_gap(self) -> None:
        summary = summarize_record_for_snapshot(_record("2026-07-31T10:00:00+00:00"))
        assert summary["feedback_status"] == "unmarked"
        assert summary["instruments"] == ["a:510300"]


@pytest.fixture
def engine(tmp_path):
    (tmp_path / "advice").mkdir()
    config = deepcopy(MINIMAL_CONFIG)
    config["paths"]["local_data_dir"] = str(tmp_path)
    with patch("stocks.engine.load_engine_config", return_value=config):
        instance = StocksEngine()
    return instance


def _save(engine, record: dict) -> None:
    engine.persistence.save_advice(AdviceRecord.from_dict(record))


class TestLedgerWrite:
    def test_mark_latest_and_list(self, engine) -> None:
        _save(engine, _record("2026-07-30T10:00:00+00:00"))
        _save(engine, _record("2026-07-31T10:00:00+00:00"))

        updated = engine.mark_advice_feedback("latest", "accepted", note="已执行")
        assert updated["feedback"]["status"] == "accepted"
        assert updated["created_at"] == "2026-07-31T10:00:00+00:00"

        records = engine.list_advice()
        assert records[0]["feedback"]["status"] == "accepted"
        assert records[1]["feedback"] is None  # 另一条不受影响

    def test_mark_by_created_at_prefix(self, engine) -> None:
        _save(engine, _record("2026-07-30T10:00:00+00:00"))
        _save(engine, _record("2026-07-31T10:00:00+00:00"))

        updated = engine.mark_advice_feedback("2026-07-30", "rejected", note="信息不足")
        assert updated["created_at"] == "2026-07-30T10:00:00+00:00"
        assert updated["feedback"]["status"] == "rejected"

    def test_unknown_ref_and_invalid_status(self, engine) -> None:
        _save(engine, _record("2026-07-30T10:00:00+00:00"))
        with pytest.raises(ValueError, match="no advice record matches"):
            engine.mark_advice_feedback("2025-01-01", "accepted")
        with pytest.raises(ValueError, match="feedback status"):
            engine.mark_advice_feedback("latest", "loved_it")

    def test_empty_ledger_error(self, engine) -> None:
        with pytest.raises(ValueError, match="empty"):
            engine.mark_advice_feedback("latest", "accepted")

    def test_engine_rollup_reads_ledger(self, engine) -> None:
        _save(engine, _record(
            _recent(1),
            feedback=make_feedback("partial", "部分执行"),
        ))
        _save(engine, _record(_recent(2)))
        rollup = engine.advice_feedback_rollup(7)
        assert rollup["marked_total"] == 1
        assert rollup["unmarked"] == 1


class TestSnapshotReflow:
    def test_advice_facts_reach_snapshot(self) -> None:
        from dataclasses import replace

        context = replace(
            _minimal_context(),
            recent_advice=[
                _record(
                    _recent(1),
                    feedback={"status": "accepted", "note": "好",
                              "marked_at": _recent(0)},
                ),
                _record(_recent(2)),
            ],
        )
        snapshot = build_unified_snapshot(context, session="cn_after_close")
        advice_facts = [f for f in snapshot.profile if f.metric == "advice_outcome"]
        assert len(advice_facts) == 2
        statuses = {f.value["feedback_status"] for f in advice_facts}
        assert statuses == {"accepted", "unmarked"}
        rollup_facts = [f for f in snapshot.profile if f.metric == "advice_feedback_rollup_7d"]
        assert len(rollup_facts) == 1
        assert rollup_facts[0].value["marked_total"] >= 0  # 窗口外标记也允许为 0 但有标记才生成

    def test_no_recent_advice_no_facts(self) -> None:
        context = _minimal_context()
        snapshot = build_unified_snapshot(context, session="cn_after_close")
        assert not [f for f in snapshot.profile if f.metric.startswith("advice_")]


class TestCLI:
    def _adapter(self, engine):
        from stocks.adapters.cli import CLIAdapter

        return CLIAdapter(engine)

    def test_feedback_requires_confirmed(self, engine, capsys) -> None:
        _save(engine, _record("2026-07-31T10:00:00+00:00"))
        self._adapter(engine).run(["--advice-feedback", "latest", "accepted"])
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is False
        assert "--confirmed" in out["error"]
        assert engine.list_advice()[0]["feedback"] is None

    def test_feedback_confirmed_writes(self, engine, capsys) -> None:
        _save(engine, _record("2026-07-31T10:00:00+00:00"))
        self._adapter(engine).run(
            ["--advice-feedback", "latest", "accepted", "--note", "smoke", "--confirmed"]
        )
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is True
        assert out["action"] == "advice_feedback_marked"
        assert engine.list_advice()[0]["feedback"]["note"] == "smoke"

    def test_rollup_readonly(self, engine, capsys) -> None:
        _save(engine, _record(_recent(1),
                              feedback=make_feedback("deferred", "下周再看")))
        self._adapter(engine).run(["--advice-rollup"])
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is True
        assert out["data"]["by_status"]["deferred"] == 1
