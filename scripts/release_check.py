#!/usr/bin/env python3
"""Release check script for stocks-claw.

Automates the pre-release checklist defined in RELEASE.md.
Exits with non-zero status if any blocking check fails.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_version_from_init() -> str:
    init_path = REPO_ROOT / "stocks" / "__init__.py"
    text = init_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("__version__"):
            return ast.literal_eval(line.split("=", 1)[1].strip())
    raise RuntimeError("__version__ not found in stocks/__init__.py")


def _read_version_from_pyproject() -> str:
    path = REPO_ROOT / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise RuntimeError('version not found in pyproject.toml')
    return m.group(1)


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd or REPO_ROOT,
        check=False,
    )


def check_version_consistency() -> bool:
    print("[1/5] Version consistency...", end=" ")
    try:
        v_init = _read_version_from_init()
        v_proj = _read_version_from_pyproject()
    except Exception as exc:
        print(f"FAIL ({exc})")
        return False
    if v_init != v_proj:
        print(f"FAIL (stocks/__init__.py={v_init} vs pyproject.toml={v_proj})")
        return False
    print(f"OK ({v_init})")
    return True


def check_ruff() -> bool:
    print("[2/5] ruff check stocks tests...", end=" ")
    proc = _run([sys.executable, "-m", "ruff", "check", "stocks", "tests"])
    if proc.returncode != 0:
        print(f"FAIL (exit {proc.returncode})")
        if proc.stdout:
            print(proc.stdout)
        if proc.stderr:
            print(proc.stderr)
        return False
    print("OK")
    return True


def check_pytest() -> bool:
    print("[3/5] pytest -q...", end=" ")
    proc = _run([sys.executable, "-m", "pytest", "-q"])
    if proc.returncode != 0:
        print(f"FAIL (exit {proc.returncode})")
        if proc.stdout:
            print(proc.stdout)
        if proc.stderr:
            print(proc.stderr)
        return False
    print("OK")
    return True


def check_compileall() -> bool:
    print("[4/5] compileall -q stocks tests...", end=" ")
    proc = _run([sys.executable, "-m", "compileall", "-q", "stocks", "tests"])
    if proc.returncode != 0:
        print(f"FAIL (exit {proc.returncode})")
        if proc.stdout:
            print(proc.stdout)
        if proc.stderr:
            print(proc.stderr)
        return False
    print("OK")
    return True


def check_smoke() -> bool:
    print("[5/5] Smoke test (CLI --output json --no-news --no-quotes)...", end=" ")
    proc = _run(
        [
            sys.executable,
            "-m",
            "stocks.adapters.cli",
            "--output",
            "json",
            "--no-news",
            "--no-quotes",
        ]
    )
    if proc.returncode != 0:
        print(f"FAIL (exit {proc.returncode})")
        if proc.stdout:
            print(proc.stdout)
        if proc.stderr:
            print(proc.stderr)
        return False
    print("OK")
    return True


def main() -> int:
    print(f"Running release checks in {REPO_ROOT}\n")
    results = [
        check_version_consistency(),
        check_ruff(),
        check_pytest(),
        check_compileall(),
        check_smoke(),
    ]
    print()
    if all(results):
        print("All checks passed. Ready to tag.")
        return 0
    failed = sum(1 for r in results if not r)
    print(f"{failed}/{len(results)} check(s) failed. Fix before tagging.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
