"""Economic event watcher — monitor calendar for post-release trigger windows.

Integrates with EventCalendar providers to detect when a scheduled economic event
(CPI, FOMC, NFP, etc.) has just been released and the system should perform an
immediate intelligence harvest.

Design decisions:
- No narrative classification — this is a pure timing/detection layer.
- Refresh windows are configurable per event_type.
- Cooldown prevents duplicate triggers within the same window.
- Bypasses EventCalendar's expiration filter (which only shows future events).
  Instead, directly queries providers for the full event list and does its own
  window calculation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from stocks.domain.models import Instrument, UpcomingEvent
from stocks.engine.event_calendar import EventCalendar, _parse_scheduled_at
from stocks.logging_utils import get_logger

logger = get_logger("economic_event_watcher")

DEFAULT_REFRESH_SECONDS = {
    "macro_release": 300,
    "central_bank": 300,
    "earnings": 600,
    "other": 900,
}


@dataclass
class EventTrigger:
    """A detected calendar event that has entered its post-release window."""
    event: UpcomingEvent
    scheduled_at: datetime
    trigger_window_start: datetime
    trigger_window_end: datetime
    minutes_since_event: float
    reason: str


@dataclass
class TriggerCheck:
    """Result of checking for event triggers."""
    checked_at: datetime
    triggered: list[EventTrigger]
    upcoming: list[UpcomingEvent]
    calendar_quality: dict
    cooldown_active: list[str]

    @property
    def has_triggers(self) -> bool:
        return len(self.triggered) > 0


class EconomicEventWatcher:
    """Watch economic calendar for events entering their post-release window."""

    def __init__(
        self,
        event_calendar: EventCalendar,
        *,
        refresh_seconds: Optional[dict[str, int]] = None,
        cooldown_minutes: int = 30,
        lookback_hours: int = 2,
    ):
        self.calendar = event_calendar
        self.refresh_windows: dict[str, int] = {**DEFAULT_REFRESH_SECONDS}
        if refresh_seconds:
            self.refresh_windows.update(refresh_seconds)
        self.cooldown_minutes = max(1, int(cooldown_minutes))
        self.lookback_hours = max(1, int(lookback_hours))
        self._last_triggered: dict[str, datetime] = {}

    async def check(
        self,
        *,
        now: Optional[datetime] = None,
        watchlist: Optional[list[Instrument]] = None,
    ) -> TriggerCheck:
        """Check the calendar for events currently in their post-release window.

        Uses a wide date window (lookback → lookahead) to catch events that have
        just passed and are still in their trigger window.
        """
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        watchlist = watchlist or []

        # Use a wide window: from lookback_hours ago to calendar's full lookahead.
        # This way we catch events whose scheduled time has passed but are still
        # in the trigger window.
        lookback = current - timedelta(hours=self.lookback_hours)
        lookahead = current + timedelta(days=self.calendar.lookahead_days)

        # Query each provider directly with the extended window
        all_events: list[UpcomingEvent] = []
        errors: dict[str, str] = {}
        for provider in self.calendar._providers:
            try:
                provider_events = await provider.fetch(
                    start=lookback.date(),
                    end=lookahead.date(),
                    watchlist=watchlist,
                )
                all_events.extend(provider_events)
            except Exception as exc:
                errors[provider.name] = f"{type(exc).__name__}: {exc}"
                logger.warning(f"Event provider {provider.name} failed in watcher: {exc}")

        triggered: list[EventTrigger] = []
        upcoming: list[UpcomingEvent] = []
        cooldown: list[str] = []

        for event in all_events:
            scheduled_at = _parse_scheduled_at(event)
            if scheduled_at is None:
                # date-only event — show as upcoming if in the future
                from stocks.engine.event_calendar import _parse_date
                event_date = _parse_date(event.date)
                if event_date and event_date >= current.date():
                    upcoming.append(event)
                continue

            refresh_sec = self.refresh_windows.get(event.event_type, self.refresh_windows["other"])
            window_start = scheduled_at
            window_end = scheduled_at + timedelta(seconds=refresh_sec)

            if window_start <= current <= window_end:
                # In trigger window — check cooldown
                event_key = event.name
                last = self._last_triggered.get(event_key)
                if last is not None:
                    if current - last < timedelta(minutes=self.cooldown_minutes):
                        cooldown.append(event.name)
                        continue

                minutes_since = (current - scheduled_at).total_seconds() / 60.0
                triggered.append(
                    EventTrigger(
                        event=event,
                        scheduled_at=scheduled_at,
                        trigger_window_start=window_start,
                        trigger_window_end=window_end,
                        minutes_since_event=round(minutes_since, 1),
                        reason=(
                            f"{event.name} 发布于 {minutes_since:.0f} 分钟前 "
                            f"(刷新窗口: {refresh_sec // 60} 分钟)"
                        ),
                    )
                )
            elif scheduled_at > current:
                # Future event
                upcoming.append(event)

        # Build quality info
        quality = {
            "status": "ok" if not errors else "partial",
            "lookback_hours": self.lookback_hours,
            "lookahead_days": self.calendar.lookahead_days,
            "event_count": len(all_events),
            "triggered_count": len(triggered),
            "upcoming_count": len(upcoming),
            "sources": {},
            "errors": errors,
        }

        return TriggerCheck(
            checked_at=current,
            triggered=triggered,
            upcoming=upcoming,
            calendar_quality=quality,
            cooldown_active=cooldown,
        )

    def mark_triggered(self, event_name: str, *, at: Optional[datetime] = None) -> None:
        """Record that an event has been acted upon, starting its cooldown.

        Args:
            event_name: Name of the event to mark.
            at: Time to record (default: now). Useful for testing with simulated time.
        """
        self._last_triggered[event_name] = (
            at.astimezone(timezone.utc) if at else datetime.now(timezone.utc)
        )
        logger.info(
            f"EconomicEventWatcher: marked '{event_name}' as triggered (cooldown started)"
        )

    def mark_all_triggered(self, triggers: list[EventTrigger], *, at: Optional[datetime] = None) -> None:
        """Mark all triggers in a TriggerCheck as acted upon.

        Args:
            triggers: List of EventTrigger objects to mark.
            at: Time to record (default: now). Useful for testing with simulated time.
        """
        for trigger in triggers:
            self.mark_triggered(trigger.event.name, at=at)
