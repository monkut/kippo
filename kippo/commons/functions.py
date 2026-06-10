import datetime
from calendar import monthrange

from django.utils import timezone

DECEMBER = 12


def get_current_month_date_range() -> tuple[timezone.datetime, timezone.datetime]:
    """Get the start and end datetime.date objects of the current month."""
    today = timezone.localtime()
    start_date = today.replace(day=1)
    _, last_day = monthrange(today.year, today.month)
    end_date = today.replace(day=last_day)
    return start_date, end_date


def first_of_month(reference: datetime.date) -> datetime.date:
    """Return the first day of the month containing `reference`."""
    return reference.replace(day=1)


def first_of_next_month(reference: datetime.date) -> datetime.date:
    """Return the first day of the month after the month of `reference`."""
    year, month = reference.year, reference.month
    if month == DECEMBER:
        return datetime.date(year + 1, 1, 1)
    return datetime.date(year, month + 1, 1)
