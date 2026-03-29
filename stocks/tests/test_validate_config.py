from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.validators import validate_all


if __name__ == '__main__':
    validate_all(['a', 'us'])
    print('config validate ok')
