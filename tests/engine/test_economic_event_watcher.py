"""Tests for EconomicEventWatcher."""

from datetime import datetime, timezone

import pytest

from stocks.domain.models import UpcomingEvent
from stocks.engine.economic_event_watcher import (
    EconomicEventWatcher,
)
from stocks.engine.event_calendar import (
    EventCalendar,
    StaticEventCalendarProvider,
)


def make_event(name, date_str, time_utc, event_type="macro_release"):
    """Helper to create an UpcomingEvent with a scheduled time."""
    from stocks.engine.event_calendar import _parse_date, _parse_time_utc
    event_date = _parse_date(date_str)
    event_time = _parse_time_utc(time_utc)
    if event_date and event_time:
        scheduled = datetime.combine(event_date, event_time).replace(tzinfo=timezone.utc)
    else:
        scheduled = None
    return UpcomingEvent(
        date=date_str,
        name=name,
        event_type=event_type,
        market="us",
        time_utc=time_utc,
        scheduled_at=scheduled.isoformat() if scheduled else None,
        time_precision="datetime" if scheduled else "date",
        source="static_config",
        affected_categories=["equity_us", "bond", "gold"],
        note="test event",
    )


class TestEconomicEventWatcher:
    """Test the watcher's trigger detection logic."""

    @pytest.mark.asyncio
    async def test_no_events_returns_empty(self):
        """Watcher with empty calendar returns no triggers."""
        provider = StaticEventCalendarProvider({"events": []})
        calendar = EventCalendar([provider])
        watcher = EconomicEventWatcher(calendar)

        check = await watcher.check(
            now=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc)
        )
        assert not check.has_triggers
        assert len(check.triggered) == 0

    @pytest.mark.asyncio
    async def test_event_in_window_triggers(self):
        """Event 2 minutes past scheduled time is detected."""
        config = {
            "events": [
                {
                    "date": "2026-07-14",
                    "time_utc": "12:30",
                    "name": "US June CPI",
                    "event_type": "macro_release",
                    "market": "us",
                    "affected_categories": ["equity_us"],
                    "note": "CPI release",
                }
            ]
        }
        provider = StaticEventCalendarProvider(config)
        calendar = EventCalendar([provider])
        watcher = EconomicEventWatcher(calendar)

        check = await watcher.check(
            now=datetime(2026, 7, 14, 12, 32, tzinfo=timezone.utc)
        )
        assert check.has_triggers
        assert len(check.triggered) == 1
        trigger = check.triggered[0]
        assert trigger.event.name == "US June CPI"
        assert trigger.minutes_since_event == 2.0
        assert "2 分钟前" in trigger.reason

    @pytest.mark.asyncio
    async def test_event_before_scheduled_no_trigger(self):
        """Event not yet released does not trigger."""
        config = {
            "events": [
                {
                    "date": "2026-07-14",
                    "time_utc": "12:30",
                    "name": "US June CPI",
                    "event_type": "macro_release",
                    "market": "us",
                    "affected_categories": ["equity_us"],
                }
            ]
        }
        provider = StaticEventCalendarProvider(config)
        calendar = EventCalendar([provider])
        watcher = EconomicEventWatcher(calendar)

        check = await watcher.check(
            now=datetime(2026, 7, 14, 12, 29, tzinfo=timezone.utc)
        )
        assert not check.has_triggers
        assert len(check.upcoming) == 1

    @pytest.mark.asyncio
    async def test_event_after_window_no_trigger(self):
        """Event past its refresh window does not trigger."""
        config = {
            "events": [
                {
                    "date": "2026-07-14",
                    "time_utc": "12:30",
                    "name": "US June CPI",
                    "event_type": "macro_release",
                    "market": "us",
                    "affected_categories": ["equity_us"],
                }
            ]
        }
        provider = StaticEventCalendarProvider(config)
        calendar = EventCalendar([provider])
        watcher = EconomicEventWatcher(calendar)

        check = await watcher.check(
            now=datetime(2026, 7, 14, 12, 36, tzinfo=timezone.utc)
        )
        assert not check.has_triggers

    @pytest.mark.asyncio
    async def test_cooldown_prevents_repeat_trigger(self):
        """Second check within cooldown period does not re-trigger."""
        config = {
            "events": [
                {
                    "date": "2026-07-14",
                    "time_utc": "12:30",
                    "name": "US June CPI",
                    "event_type": "macro_release",
                    "market": "us",
                    "affected_categories": ["equity_us"],
                }
            ]
        }
        provider = StaticEventCalendarProvider(config)
        calendar = EventCalendar([provider])
        watcher = EconomicEventWatcher(calendar, cooldown_minutes=30)

        now = datetime(2026, 7, 14, 12, 32, tzinfo=timezone.utc)

        # First check triggers
        check1 = await watcher.check(now=now)
        assert check1.has_triggers
        watcher.mark_all_triggered(check1.triggered, at=now)

        # Second check within cooldown should NOT trigger
        check2 = await watcher.check(now=now)
        assert not check2.has_triggers
        assert len(check2.cooldown_active) == 1

    @pytest.mark.asyncio
    async def test_different_event_types_have_different_windows(self):
        """Central bank events have appropriate window compared to macro releases."""
        config = {
            "events": [
                {
                    "date": "2026-07-29",
                    "time_utc": "18:00",
                    "name": "FOMC Decision",
                    "event_type": "central_bank",
                    "market": "us",
                    "affected_categories": ["equity_us"],
                }
            ]
        }
        provider = StaticEventCalendarProvider(config)
        calendar = EventCalendar([provider])
        # Override: central_bank gets 10 min window
        watcher = EconomicEventWatcher(
            calendar,
            refresh_seconds={"central_bank": 600},
        )

        # 8 minutes after FOMC — should trigger (within 10 min window)
        check = await watcher.check(
            now=datetime(2026, 7, 29, 18, 8, tzinfo=timezone.utc)
        )
        assert check.has_triggers

    @pytest.mark.asyncio
    async def test_multiple_events_same_window(self):
        """Two events in the same window both trigger."""
        config = {
            "events": [
                {
                    "date": "2026-07-14",
                    "time_utc": "12:30",
                    "name": "US June CPI",
                    "event_type": "macro_release",
                    "market": "us",
                    "affected_categories": ["equity_us"],
                },
                {
                    "date": "2026-07-14",
                    "time_utc": "12:30",
                    "name": "US June PPI",
                    "event_type": "macro_release",
                    "market": "us",
                    "affected_categories": ["equity_us"],
                },
            ]
        }
        provider = StaticEventCalendarProvider(config)
        calendar = EventCalendar([provider])
        watcher = EconomicEventWatcher(calendar)

        check = await watcher.check(
            now=datetime(2026, 7, 14, 12, 33, tzinfo=timezone.utc)
        )
        assert check.has_triggers
        assert len(check.triggered) == 2
