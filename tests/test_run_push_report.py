from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.test_push_payload import _artifact, _full_outlook_artifact

SCRIPT = Path(__file__).parents[1] / "scripts" / "run_push_report.py"


def test_entrypoint_renders_and_persists_sanitized_payload(tmp_path):
    root = tmp_path / "latest"
    root.mkdir()
    (root / "cn_after_close.json").write_text(json.dumps(_artifact(), ensure_ascii=False))
    payload = tmp_path / "payload"
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--session",
            "cn_after_close",
            "--artifact-root",
            str(root),
            "--payload-root",
            str(payload),
            "--now",
            "2026-07-17T15:27:00+08:00",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "**交易指令卡**" in r.stdout and "**私人投资助理**" in r.stdout
    assert "4个减仓信号" not in r.stdout
    data = json.loads((payload / "cn_after_close.json").read_text())
    assert set(data) == {"payload_version", "session_label", "market_date", "delivery", "user_view"}


def test_entrypoint_fails_loudly_on_invalid_or_stale_artifact(tmp_path):
    root = tmp_path / "latest"
    root.mkdir()
    bad = _artifact()
    bad["generated_at"] = "2026-07-16T00:00:00+00:00"
    (root / "cn_after_close.json").write_text(json.dumps(bad))
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--session",
            "cn_after_close",
            "--artifact-root",
            str(root),
            "--payload-root",
            str(tmp_path / "payload"),
            "--now",
            "2026-07-17T15:27:00+08:00",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert r.stdout == ""
    assert "INVALID:" in r.stderr
    assert not (tmp_path / "payload" / "cn_after_close.json").exists()


def _outlook_test_artifact():
    """Helper for outlook entrypoint tests."""
    from tests.test_push_payload import _full_outlook_artifact
    return _full_outlook_artifact()


def test_entrypoint_renders_outlook_section(tmp_path):
    """Full pipeline renders the outlook section."""
    root = tmp_path / "latest"
    root.mkdir()
    (root / "cn_after_close.json").write_text(
        json.dumps(_full_outlook_artifact(), ensure_ascii=False)
    )
    payload_dir = tmp_path / "payload"
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--session", "cn_after_close",
            "--artifact-root", str(root),
            "--payload-root", str(payload_dir),
            "--now", "2026-07-17T15:27:00+08:00",
        ],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "**中长期研判**" in r.stdout
    assert "**未来1–2周**" in r.stdout
