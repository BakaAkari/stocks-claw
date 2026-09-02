import json
import sys
sys.path.insert(0, ".")
from stocks.engine.presentation import format_upcoming_events


def test_format_upcoming_events_deterministic():
    """事件表确定性格式化: 倒计时/类别标签, 截断 8 条, 容错非 dict。"""
    events = [
        {"date": "2026-09-04", "name": "美国 8 月非农就业报告", "event_type": "macro_release",
         "days_until": 2, "market": "us", "note": "就业强弱直接影响利率预期"},
        {"date": "2026-09-16", "name": "FOMC 利率决议", "event_type": "central_bank",
         "days_until": 14, "market": "us"},
        {"date": "2026-09-05", "name": "DELL 财报", "event_type": "earnings",
         "days_until": 0, "market": "us"},
        "garbage-entry",
    ]
    out = format_upcoming_events(events)
    assert len(out) == 3
    assert out[0]["countdown"] == "2天后"
    assert out[0]["event_type_label"] == "宏观数据发布"
    assert out[1]["event_type_label"] == "央行决议"
    assert out[2]["countdown"] == "今天"
    assert out[2]["event_type_label"] == "财报"
    # 容错: 空输入
    assert format_upcoming_events(None) == []
    assert format_upcoming_events([]) == []


def test_event_calendar_split_window():
    """分窗 lookahead: 财报短窗, 央行/宏观长窗。"""
    import asyncio
    from datetime import datetime, timezone, timedelta
    from stocks.domain.models import UpcomingEvent
    from stocks.engine.event_calendar import EventCalendar

    class FakeProvider:
        name = "fake"
        async def fetch(self, *, start, end, watchlist):
            return [
                UpcomingEvent(date="2026-10-14", name="远期CPI", event_type="macro_release",
                              market="us"),
                UpcomingEvent(date="2026-10-01", name="远期财报", event_type="earnings",
                              market="us"),
                UpcomingEvent(date="2026-09-05", name="近期CPI", event_type="macro_release",
                              market="us"),
            ]

    cal = EventCalendar([FakeProvider()], lookahead_days=14,
                        macro_lookahead_days=45, earnings_lookahead_days=14)
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    events, quality = asyncio.run(cal.fetch(now=now, watchlist=[]))
    names = [e.name for e in events]
    # 远期CPI(42天) 在长窗内, 远期财报(29天) 超出短窗被过滤, 近期CPI保留
    assert "远期CPI" in names
    assert "近期CPI" in names
    assert "远期财报" not in names
