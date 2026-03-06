from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.services.business_day import (
    is_business_day,
    previous_business_day,
    get_clipping_window,
    KST,
)

# 2025-01-01 is New Year's Day (holiday in Korea)
# 2025-01-04 is Saturday
# 2025-01-06 is Monday


def test_is_business_day_weekday():
    # Monday
    dt = datetime(2025, 1, 6, tzinfo=KST)
    assert is_business_day(dt) is True


def test_is_business_day_weekend():
    # Saturday
    dt = datetime(2025, 1, 4, tzinfo=KST)
    assert is_business_day(dt) is False


def test_is_business_day_holiday():
    # New Year's Day
    dt = datetime(2025, 1, 1, tzinfo=KST)
    assert is_business_day(dt) is False


def test_previous_business_day_from_monday():
    # Monday -> previous Friday
    dt = datetime(2025, 1, 6, tzinfo=KST)
    prev = previous_business_day(dt)
    assert prev.weekday() == 4  # Friday
    assert prev.date().day == 3


def test_previous_business_day_from_tuesday():
    dt = datetime(2025, 1, 7, tzinfo=KST)
    prev = previous_business_day(dt)
    assert prev.date().day == 6  # Monday


def test_clipping_window_normal_weekday():
    now = datetime(2025, 1, 7, 11, 0, tzinfo=KST)  # Tuesday 11:00
    date_from, date_to = get_clipping_window(now)
    assert date_from == datetime.combine(
        datetime(2025, 1, 6).date(), time(10), tzinfo=KST
    )
    assert date_to == datetime.combine(
        datetime(2025, 1, 7).date(), time(10), tzinfo=KST
    )


def test_clipping_window_monday():
    now = datetime(2025, 1, 6, 11, 0, tzinfo=KST)  # Monday
    date_from, date_to = get_clipping_window(now)
    # Previous business day is Friday Jan 3
    assert date_from.date().day == 3
    assert date_from.date().weekday() == 4  # Friday
