from pathlib import Path
import sys
import subprocess

ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT / 'stocks' / 'reports'
LATEST_PATH = REPORTS_DIR / 'personal-latest.md'

if __name__ == '__main__':
    out = subprocess.check_output(
        [sys.executable, str(ROOT / 'stocks' / 'cli' / 'send_llm_report.py'), '--save'],
        text=True,
    )
    assert '今日概览' in out
    assert '风险偏好' in out
    assert '美股观察' in out
    assert '一句话结论' in out
    assert '[saved]' in out
    assert LATEST_PATH.exists()
    latest = LATEST_PATH.read_text(encoding='utf-8')
    assert '今日概览' in latest
    print('send llm report ok')
