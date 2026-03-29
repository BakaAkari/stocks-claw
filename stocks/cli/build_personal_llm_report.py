#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.personal_llm_report_service import PersonalLLMReportService


def main():
    print(PersonalLLMReportService().generate())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
