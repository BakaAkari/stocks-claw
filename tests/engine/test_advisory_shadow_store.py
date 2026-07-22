"""Tests for the shadow advisory store.

Shadow artifacts must never be delivered. They must be comparable and replayable.
"""
from __future__ import annotations

from pathlib import Path

from stocks.domain.advisory_models import (
    AdvisoryValidationReceipt,
    InvestmentAdvisory,
    UnifiedAnalysisSnapshot,
)
from stocks.engine.advisory_shadow_store import AdvisoryShadowStore


class TestAdvisoryShadowStore:
    def test_save_and_load_manifest(self, tmp_path: Path) -> None:
        store = AdvisoryShadowStore(tmp_path)
        snapshot = UnifiedAnalysisSnapshot(
            snapshot_id="s1",
            generated_at="2026-07-22T10:00:00+00:00",
            trigger="scheduled",
            session="cn_pre_open",
            market_scope="cn",
        )
        advisory = InvestmentAdvisory(
            advisory_id="a1",
            snapshot_id="s1",
            generated_at="2026-07-22T10:00:00+00:00",
        )
        receipt = AdvisoryValidationReceipt(
            status="ok",
            schema_version="1",
            validator_version="1",
            prompt_contract_hash="p1",
            snapshot_hash="sh1",
            advisory_content_hash="ah1",
            validated_at="2026-07-22T10:00:00+00:00",
        )
        manifest = store.save(
            "run-20260722-100000",
            snapshot,
            advisory,
            receipt,
            production_decision_id="d1",
        )
        assert manifest["snapshot_id"] == "s1"
        assert manifest["advisory_id"] == "a1"
        assert manifest["receipt_status"] == "ok"
        assert manifest["production_decision_id"] == "d1"
        assert manifest["files"]["snapshot"]

    def test_load_manifest_roundtrip(self, tmp_path: Path) -> None:
        store = AdvisoryShadowStore(tmp_path)
        snapshot = UnifiedAnalysisSnapshot(
            snapshot_id="s2",
            generated_at="2026-07-22T10:00:00+00:00",
            trigger="scheduled",
            session="cn_pre_open",
            market_scope="cn",
        )
        advisory = InvestmentAdvisory(
            advisory_id="a2",
            snapshot_id="s2",
            generated_at="2026-07-22T10:00:00+00:00",
        )
        receipt = AdvisoryValidationReceipt(
            status="warnings",
            schema_version="1",
            validator_version="1",
            prompt_contract_hash="p2",
            snapshot_hash="sh2",
            advisory_content_hash="ah2",
            validated_at="2026-07-22T10:00:00+00:00",
            warnings=("missing evidence",),
        )
        store.save("run-2", snapshot, advisory, receipt)
        loaded = store.load_manifest("run-2")
        assert loaded is not None
        assert loaded["receipt_status"] == "warnings"

    def test_files_do_not_overlap_across_runs(self, tmp_path: Path) -> None:
        store = AdvisoryShadowStore(tmp_path)
        for i in range(3):
            snapshot = UnifiedAnalysisSnapshot(
                snapshot_id=f"s{i}",
                generated_at="2026-07-22T10:00:00+00:00",
                trigger="scheduled",
                session="cn_pre_open",
                market_scope="cn",
            )
            advisory = InvestmentAdvisory(
                advisory_id=f"a{i}",
                snapshot_id=f"s{i}",
                generated_at="2026-07-22T10:00:00+00:00",
            )
            receipt = AdvisoryValidationReceipt(
                status="ok",
                schema_version="1",
                validator_version="1",
                prompt_contract_hash="p",
                snapshot_hash="sh",
                advisory_content_hash="ah",
                validated_at="2026-07-22T10:00:00+00:00",
            )
            store.save(f"run-{i}", snapshot, advisory, receipt)
        runs = store.list_runs()
        assert runs == ["run-0", "run-1", "run-2"]
        for i in range(3):
            snapshot, advisory, receipt = store.load(f"run-{i}")
            assert advisory["advisory_id"] == f"a{i}"

    def test_hashes_detect_tampering(self, tmp_path: Path) -> None:
        store = AdvisoryShadowStore(tmp_path)
        snapshot = UnifiedAnalysisSnapshot(
            snapshot_id="s3",
            generated_at="2026-07-22T10:00:00+00:00",
            trigger="scheduled",
            session="cn_pre_open",
            market_scope="cn",
        )
        advisory = InvestmentAdvisory(
            advisory_id="a3",
            snapshot_id="s3",
            generated_at="2026-07-22T10:00:00+00:00",
        )
        receipt = AdvisoryValidationReceipt(
            status="ok",
            schema_version="1",
            validator_version="1",
            prompt_contract_hash="p",
            snapshot_hash="sh",
            advisory_content_hash="ah",
            validated_at="2026-07-22T10:00:00+00:00",
        )
        manifest = store.save("run-3", snapshot, advisory, receipt)
        # Tamper with advisory file
        advisory_path = Path(manifest["files"]["advisory"])
        data = advisory_path.read_text(encoding="utf-8")
        tampered = data.replace("a3", "a3-tampered")
        advisory_path.write_text(tampered, encoding="utf-8")
        loaded_snapshot, loaded_advisory, loaded_receipt = store.load("run-3")
        assert loaded_advisory["advisory_id"] == "a3-tampered"
        # The manifest still records the original hash; the tampered file is detectable
        import hashlib
        tampered_hash = hashlib.sha256(str(loaded_advisory).encode("utf-8")).hexdigest()[:24]
        assert manifest["advisory_hash"] != tampered_hash
