import datetime
import uuid
from calendar import monthrange

from django.conf import settings
from django.utils import timezone

DECEMBER = 12


def is_uuid(value: str) -> bool:
    """True if `value` is a valid UUID string."""
    try:
        uuid.UUID(value)
    except (ValueError, TypeError):
        return False
    return True


def ui_url(path: str) -> str:
    """Absolute-or-relative URL to a kippo-ui SPA route, e.g. ``ui_url("billing?month=2026-04")``.

    Prefixes ``settings.UI_BASE_URL`` (empty in production → same-origin; the Vite dev server origin
    in local dev) + ``settings.URL_PREFIX`` + ``/ui/``. Use for every admin→UI deep-link so the
    local-dev origin fix applies uniformly.
    """
    return f"{settings.UI_BASE_URL}{settings.URL_PREFIX}/ui/{path.lstrip('/')}"


def local_date(value: datetime.datetime) -> datetime.date:
    """The calendar date of `value` in the current time zone (JST).

    `value.date()` on an aware datetime returns the date in that datetime's own zone: for a value
    read from the database or built with `timezone.now()` that is UTC, which is still yesterday for
    the first nine hours of a JST day. A naive datetime is taken as already local.
    """
    return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()


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


def last_of_month(reference: datetime.date) -> datetime.date:
    """Return the last day of the month containing `reference`."""
    _, last_day = monthrange(reference.year, reference.month)
    return reference.replace(day=last_day)


def add_months(reference: datetime.date, months: int) -> datetime.date:
    """Return `reference` shifted by `months`, clamping the day to the target month's last day (01-31 + 1 → 02-28)."""
    month_index = reference.year * 12 + reference.month - 1 + months
    year, month = divmod(month_index, 12)
    return datetime.date(year, month + 1, min(reference.day, monthrange(year, month + 1)[1]))
