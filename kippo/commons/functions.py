from calendar import monthrange

from django.utils import timezone


def get_current_month_date_range() -> tuple[timezone.datetime, timezone.datetime]:
    """Get the start and end datetime.date objects of the current month."""
    today = timezone.localtime()
    start_date = today.replace(day=1)
    _, last_day = monthrange(today.year, today.month)
    end_date = today.replace(day=last_day)
    return start_date, end_date
