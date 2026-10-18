"""Confirmation events must match current user-owned content."""

import json

from scripts.smoke_decision_review import _files_hash, _isolated_adapter
from scripts.smoke_financial_reconciliation import fixture_document, sample_facts
from stocks.engine.confirmation_provenance import ConfirmationStore, confirmation_hash


def _adapter(tmp_path):
    adapter = _isolated_adapter(tmp_path)
    doc = fixture_document()
    path = tmp_path / ".local/financial_assets.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    ConfirmationStore(tmp_path / ".local").record(
        "financial_assets", doc, channel="mcp", source_path=str(path)
    )
    adapter.engine._assets = adapter.engine._load_assets_from_file()
    return adapter


def _call(adapter, method, params=None):
    result = adapter.handle_request({"method": method, "params": params})
    assert result["success"], result
    return result["data"]


def test_old_local_profile_is_legacy_unverified(tmp_path):
    adapter = _adapter(tmp_path)
    profile = {"mandate": {"goals": "旧文件"}}
    (tmp_path / ".local/investor_profile.json").write_text(json.dumps(profile), encoding="utf-8")
    adapter.engine._profile = profile
    snapshot = _call(adapter, "personal_mandate", {})
    assert snapshot["sources"]["profile"]["kind"] == "legacy_unverified"
    assert snapshot["fields"]["goals"]["status"] == "unknown"


def test_confirmed_profile_and_constraints_create_content_bound_events(tmp_path):
    adapter = _adapter(tmp_path)
    _, _, _, _, mandate = sample_facts()
    _call(adapter, "profile_update", {"profile": {"mandate": mandate}, "confirmed": True})
    _call(adapter, "constraints_replace", {"constraints": {}, "confirmed": True})
    snapshot = _call(adapter, "personal_mandate", {})
    assert snapshot["sources"]["profile"]["kind"] == "local_confirmed"
    assert snapshot["sources"]["profile"]["confirmation_event_id"]
    assert snapshot["sources"]["profile"]["confirmation_channel"] == "mcp"
    assert snapshot["sources"]["constraints"]["kind"] == "local_confirmed"
    events = sorted((tmp_path / ".local/confirmation_events").glob("*.json"))
    assert len(events) == 3
    assert sum(json.loads(path.read_text())["source_kind"] == "user_confirmed" for path in events) == 3


def test_user_confirmation_can_upgrade_same_content_after_unverified_engine_write(tmp_path):
    adapter = _adapter(tmp_path)
    adapter.engine.update_profile({"mandate": {"goals": "先写入但未确认"}})
    assert _call(adapter, "personal_mandate", {})["sources"]["profile"]["kind"] == "legacy_unverified"
    _call(adapter, "profile_update", {
        "profile": {"mandate": {"goals": "先写入但未确认"}}, "confirmed": True,
    })
    snapshot = _call(adapter, "personal_mandate", {})
    assert snapshot["sources"]["profile"]["kind"] == "local_confirmed"
    events = [json.loads(path.read_text()) for path in (tmp_path / ".local/confirmation_events").glob("*.json")]
    assert {event["source_kind"] for event in events} == {"unverified", "user_confirmed"}


def test_tampered_profile_does_not_retain_confirmation_status(tmp_path):
    adapter = _adapter(tmp_path)
    _call(adapter, "profile_update", {"profile": {"mandate": {"goals": "确认值"}}, "confirmed": True})
    profile_path = tmp_path / ".local/investor_profile.json"
    profile = json.loads(profile_path.read_text())
    profile["mandate"]["goals"] = "被改过"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    adapter.engine._profile = profile
    snapshot = _call(adapter, "personal_mandate", {})
    assert snapshot["sources"]["profile"]["kind"] == "legacy_unverified"
    assert snapshot["fields"]["goals"]["status"] == "unknown"


def test_confirmed_financial_events_are_content_bound_and_idempotent(tmp_path):
    adapter = _adapter(tmp_path)
    opening, _, _, _, _ = sample_facts()
    _call(adapter, "statement_save", {"statement": opening, "confirmed": True})
    before = _files_hash(tmp_path / ".local")
    _call(adapter, "statement_save", {"statement": opening, "confirmed": True})
    assert _files_hash(tmp_path / ".local") == before
    events = list((tmp_path / ".local/confirmation_events").glob("*.json"))
    assert len(events) == 2
    event = next(json.loads(path.read_text()) for path in events
                 if json.loads(path.read_text())["target"] == "account_statements:opening")
    assert event["target"] == "account_statements:opening"
    saved = _call(adapter, "statement_list")["records"][0]
    assert event["content_hash"] == confirmation_hash(saved, target="account_statements:opening")
    assert event["source_kind"] == "user_confirmed"


def test_unconfirmed_rejection_creates_no_confirmation_event(tmp_path):
    adapter = _adapter(tmp_path)
    _, _, _, _, mandate = sample_facts()
    before = _files_hash(tmp_path / ".local")
    result = adapter.handle_request({"method": "profile_update", "params": {"profile": {"mandate": mandate}}})
    assert result["success"] is False
    assert _files_hash(tmp_path / ".local") == before
    assert not any(
        json.loads(path.read_text())["target"] == "profile"
        for path in (tmp_path / ".local/confirmation_events").glob("*.json")
    )


def test_missing_statement_event_blocks_integrity_match(tmp_path):
    adapter = _adapter(tmp_path)
    opening, closing, fills, movement, _ = sample_facts()
    _call(adapter, "statement_save", {"statement": opening, "confirmed": True})
    _call(adapter, "statement_save", {"statement": closing, "confirmed": True})
    for fill in fills:
        _call(adapter, "execution_save", {"execution": fill, "confirmed": True})
    _call(adapter, "cash_movement_save", {"movement": movement, "confirmed": True})
    event_dir = tmp_path / ".local/confirmation_events"
    for path in event_dir.glob("*.json"):
        if json.loads(path.read_text())["target"] == "account_statements:opening":
            path.unlink()
    integrity = _call(adapter, "financial_integrity", {})
    assert integrity["status"] == "incomplete"
    assert any("confirmation_provenance_missing" in gap for gap in integrity["gaps"])


def test_missing_asset_event_blocks_integrity_match(tmp_path):
    adapter = _adapter(tmp_path)
    opening, closing, fills, movement, _ = sample_facts()
    for statement in (opening, closing):
        _call(adapter, "statement_save", {"statement": statement, "confirmed": True})
    for fill in fills:
        _call(adapter, "execution_save", {"execution": fill, "confirmed": True})
    _call(adapter, "cash_movement_save", {"movement": movement, "confirmed": True})
    for path in (tmp_path / ".local/confirmation_events").glob("*.json"):
        if json.loads(path.read_text())["target"] == "financial_assets":
            path.unlink()
    integrity = _call(adapter, "financial_integrity", {})
    assert integrity["status"] == "incomplete"
    assert "financial_assets_confirmation_missing" in integrity["gaps"]


def test_edited_confirmation_envelope_is_rejected(tmp_path):
    adapter = _adapter(tmp_path)
    _call(adapter, "profile_update", {"profile": {"mandate": {"goals": "确认值"}}, "confirmed": True})
    event_path = next(
        path for path in (tmp_path / ".local/confirmation_events").glob("*.json")
        if json.loads(path.read_text())["target"] == "profile"
    )
    event = json.loads(event_path.read_text())
    event["actor"] = "forged"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    adapter.engine._profile = json.loads((tmp_path / ".local/investor_profile.json").read_text())
    assert _call(adapter, "personal_mandate", {})["sources"]["profile"]["kind"] == "legacy_unverified"

