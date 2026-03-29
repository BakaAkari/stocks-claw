from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = ROOT / 'stocks' / 'logs' / 'stocks.jsonl'

if __name__ == '__main__':
    subprocess.run(
        ['python3', 'stocks/scripts/handle_command.py', '查A股 紫金矿业'],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    assert LOG_PATH.exists()
    text = LOG_PATH.read_text(encoding='utf-8')
    assert 'command.received' in text
    assert 'query.done' in text
    print('logging smoke ok')
