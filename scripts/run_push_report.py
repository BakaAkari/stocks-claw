#!/usr/bin/env python3
"""Fail-closed deterministic push report entrypoint for Hermes no-agent cron jobs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from build_push_payload import build_push_payload, render_push_payload, validate_payload_text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--artifact-root", default=".local/scheduled_runs/latest")
    parser.add_argument("--payload-root", default=".local/push_payloads/latest")
    parser.add_argument("--now")
    args = parser.parse_args()
    now = args.now or datetime.now().astimezone().isoformat()
    artifact_path = Path(args.artifact_root) / f"{args.session}.json"
    payload_path = Path(args.payload_root) / f"{args.session}.json"
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        payload = build_push_payload(artifact, now=now)
        text = render_push_payload(payload)
        errors = validate_payload_text(payload, text)
        if errors:
            raise ValueError("; ".join(errors))
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = payload_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(payload_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
