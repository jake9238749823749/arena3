from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from futures.sessions import SessionCalendar

NY = ZoneInfo("America/New_York")


def test_globex_weekend_and_break():
    cal = SessionCalendar()
    assert cal.is_open(datetime(2021, 3, 7, 18, 0, tzinfo=NY))  # Sunday open
    assert not cal.is_open(datetime(2021, 3, 6, 12, 0, tzinfo=NY))  # Saturday
    assert not cal.is_open(datetime(2021, 3, 8, 17, 30, tzinfo=NY))  # Mon break
    assert cal.is_open(datetime(2021, 3, 8, 18, 0, tzinfo=NY))
    assert not cal.is_open(datetime(2021, 3, 12, 17, 0, tzinfo=NY))  # Friday close


def test_session_date_night_belongs_to_next_day():
    cal = SessionCalendar()
    assert cal.session_date(datetime(2021, 3, 7, 18, 30, tzinfo=NY)).isoformat() == "2021-03-08"
    assert cal.session_date(datetime(2021, 3, 8, 10, 0, tzinfo=NY)).isoformat() == "2021-03-08"


def test_naive_timestamp_rejected():
    cal = SessionCalendar()
    with pytest.raises(ValueError):
        cal.is_open(datetime(2021, 3, 8, 10, 0))


def test_force_flat_weekdays_only():
    cal = SessionCalendar()
    start = datetime(2021, 3, 5, 0, 0, tzinfo=NY)  # Friday
    end = datetime(2021, 3, 8, 23, 0, tzinfo=NY)  # Monday
    stamps = cal.force_flat_times(start, end, "15:45")
    assert all(ts.weekday() < 5 for ts in stamps)
    assert all(ts.hour == 15 and ts.minute == 45 for ts in stamps)
