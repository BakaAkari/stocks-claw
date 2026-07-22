from pathlib import Path


def test_generator_script_is_repo_relative_and_branch_pinned():
    script = (Path(__file__).parents[1] / "scripts" / "stocks-claw-generate-due.sh").read_text()
    assert 'REPO_ROOT="${STOCKS_CLAW_REPO_ROOT:-/mnt/user/code-project/stocks-claw}"' in script
    assert 'EXPECTED_BRANCH="${STOCKS_CLAW_EXPECTED_BRANCH:-master}"' in script
    assert "stocks-claw-trust-t1" not in script
    assert "feat/decision-trust-t1" not in script
    assert '.venv/bin/python -m stocks.adapters.cli --scheduled-run-due' in script
    assert 'uv run' not in script


def test_signal_settlement_wrapper_uses_project_venv():
    script = (Path(__file__).parents[1] / "scripts" / "signal-settlement.sh").read_text()
    assert 'REPO_ROOT="${STOCKS_CLAW_REPO_ROOT:-/mnt/user/code-project/stocks-claw}"' in script
    assert "stocks-claw-trust-t1" not in script
    assert "exec .venv/bin/python scripts/signal_settlement.py" in script
