"""CME/COMEX Globex session helpers.

All wall-clock comparisons are in America/New_York. The engine stores
timezone-aware timestamps; naive values are rejected.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def ensure_ny(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError(f"naive timestamp rejected: {ts!r}")
    return ts.astimezone(NY)


def parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


class SessionCalendar:
    """Globex: Sun 18:00 ET – Fri 17:00 ET with a daily 17:00–18:00 break."""

    def __init__(
        self,
        *,
        week_open: str = "18:00",
        week_close: str = "17:00",
        break_start: str = "17:00",
        break_end: str = "18:00",
        holidays: set[date] | None = None,
    ) -> None:
        self.week_open = parse_hhmm(week_open)
        self.week_close = parse_hhmm(week_close)
        self.break_start = parse_hhmm(break_start)
        self.break_end = parse_hhmm(break_end)
        self.holidays = holidays or set()

    def is_open(self, ts: datetime) -> bool:
        local = ensure_ny(ts)
        d = local.date()
        t = local.timetz().replace(tzinfo=None) if False else local.time()
        wd = local.weekday()  # Mon=0
        if d in self.holidays:
            return False
        if wd == 5:  # Saturday
            return False
        if wd == 6:  # Sunday
            return t >= self.week_open
        if wd == 4:  # Friday
            if t >= self.week_close:
                return False
            if self.break_start <= t < self.break_end:
                return False
            return True
        # Mon–Thu
        if self.break_start <= t < self.break_end:
            return False
        return True

    def session_date(self, ts: datetime) -> date:
        """CME night session (18:00+) belongs to the next calendar date."""
        local = ensure_ny(ts)
        if local.time() >= self.break_end:
            return (local + timedelta(days=1)).date()
        return local.date()

    def is_rth(self, ts: datetime, rth_start: str, rth_end: str) -> bool:
        local = ensure_ny(ts)
        t = local.time()
        return parse_hhmm(rth_start) <= t < parse_hhmm(rth_end)

    def force_flat_times(self, start: datetime, end: datetime, hhmm: str) -> list[datetime]:
        """Every weekday ``hhmm`` America/New_York in [start, end]."""
        target = parse_hhmm(hhmm)
        local_start = ensure_ny(start).replace(hour=0, minute=0, second=0, microsecond=0)
        local_end = ensure_ny(end)
        out: list[datetime] = []
        day = local_start
        while day <= local_end:
            if day.weekday() < 5 and day.date() not in self.holidays:
                stamp = day.replace(hour=target.hour, minute=target.minute)
                if start <= stamp <= end:
                    out.append(stamp)
            day += timedelta(days=1)
        return out

    def iter_bar_closes(
        self,
        start: datetime,
        end: datetime,
        bar_minutes: int,
    ) -> list[datetime]:
        """Close timestamps of Globex bars of ``bar_minutes`` width."""
        start = ensure_ny(start)
        end = ensure_ny(end)
        # Align to the first close strictly after start-aligned open.
        ts = start
        # Snap down to bar grid in NY local minutes from midnight.
        minutes = ts.hour * 60 + ts.minute
        snapped = minutes - (minutes % bar_minutes)
        ts = ts.replace(hour=snapped // 60, minute=snapped % 60, second=0, microsecond=0)
        if ts < start:
            ts += timedelta(minutes=bar_minutes)
        out: list[datetime] = []
        delta = timedelta(minutes=bar_minutes)
        while ts <= end:
            # A bar with close ``ts`` is valid if the market was open for the
            # interval (ts - delta, ts]. Reject the daily break and weekend.
            bar_open = ts - delta
            if self.is_open(bar_open + timedelta(seconds=1)) and self.is_open(ts):
                # Also reject bars that span the 17:00–18:00 break.
                if not (bar_open.time() < self.break_start <= ts.time()):
                    out.append(ts)
            ts += delta
        return out


def try_load_cme_holidays(start: date, end: date) -> set[date]:
    """Best-effort CME holiday set. Falls back to empty if calendar missing."""
    try:
        import pandas_market_calendars as mcal

        cal = mcal.get_calendar("CME_Equity")
        sched = cal.schedule(start_date=start.isoformat(), end_date=end.isoformat())
        # Holidays are days in the range that are weekdays but not in schedule.
        have = {ts.date() for ts in sched.index}
        holidays: set[date] = set()
        d = start
        while d <= end:
            if d.weekday() < 5 and d not in have:
                holidays.add(d)
            d += timedelta(days=1)
        return holidays
    except Exception:
        return set()
