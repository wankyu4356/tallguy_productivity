from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import holidays

KST = ZoneInfo("Asia/Seoul")
CLIPPING_HOUR = 10  # 오전 10시 기준


def get_kr_holidays(year: int | None = None) -> holidays.HolidayBase:
    if year is None:
        year = datetime.now(KST).year
    return holidays.KR(years=[year - 1, year, year + 1])


def is_business_day(dt: datetime) -> bool:
    kr_holidays = get_kr_holidays(dt.year)
    return dt.weekday() < 5 and dt.date() not in kr_holidays


def previous_business_day(dt: datetime) -> datetime:
    """Find the most recent business day before dt."""
    d = dt - timedelta(days=1)
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d


def get_clipping_window(
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Return (date_from, date_to) for the clipping window.

    Window: previous business day 10:00 KST ~ today 10:00 KST.
    If today is not a business day, date_to is still today 10:00.
    """
    if now is None:
        now = datetime.now(KST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=KST)

    date_to = datetime.combine(now.date(), time(CLIPPING_HOUR), tzinfo=KST)

    prev_bd = previous_business_day(now)
    date_from = datetime.combine(prev_bd.date(), time(CLIPPING_HOUR), tzinfo=KST)

    return date_from, date_to
