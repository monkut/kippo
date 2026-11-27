import datetime
import functools
import importlib.resources
import logging
import pathlib
import uuid
import zoneinfo
from calendar import monthrange

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

DECEMBER = 12
# tz database table: one line per (country, zone) as "<ISO-3166 alpha-2>\t<coordinates>\t<zone name>[\t<comment>]"
ZONE_TABLE_FILENAME = "zone.tab"


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


def _read_zone_table() -> str:
    """Text of the tz database's zone.tab, from the system tz database or else the `tzdata` package."""
    for directory in zoneinfo.TZPATH:
        path = pathlib.Path(directory) / ZONE_TABLE_FILENAME
        if path.is_file():
            return path.read_text(encoding="utf-8")
    try:
        return importlib.resources.files("tzdata").joinpath("zoneinfo", ZONE_TABLE_FILENAME).read_text(encoding="utf-8")
    except (ModuleNotFoundError, FileNotFoundError):
        logger.warning(f"{ZONE_TABLE_FILENAME} not found in {zoneinfo.TZPATH} or the tzdata package: no timezone can be derived from a country")
        return ""


@functools.cache
def country_timezone_names() -> dict[str, tuple[str, ...]]:
    """ISO-3166 alpha-2 country code -> the tz database zones of that country."""
    zones_by_country: dict[str, list[str]] = {}
    for line in _read_zone_table().splitlines():
        if not line or line.startswith("#"):
            continue
        country_code, _coordinates, zone_name = line.split("\t")[:3]
        zones_by_country.setdefault(country_code, []).append(zone_name)
    return {country_code: tuple(zone_names) for country_code, zone_names in zones_by_country.items()}


def _keep_the_same_time(zone_names: tuple[str, ...]) -> bool:
    """True when every zone has the same UTC offset in both January and July of the current year."""
    year = timezone.localdate().year
    probes = (datetime.datetime(year, 1, 1, 12), datetime.datetime(year, 7, 1, 12))  # noqa: DTZ001 -- zone applied below
    offsets = {tuple(probe.replace(tzinfo=zoneinfo.ZoneInfo(zone_name)).utcoffset() for probe in probes) for zone_name in zone_names}
    return len(offsets) == 1


def timezone_name_for_country(alpha_2: str) -> str | None:
    """The country's timezone, when the whole country keeps one time.

    That is a country with one zone (JP -> Asia/Tokyo), or whose zones all share the same UTC offset
    year-round (DE -> Europe/Berlin; its other zone, Europe/Busingen, keeps the same time). None for
    a country spanning different times (US, AU, ES, ...) and for an unknown code.
    """
    zone_names = country_timezone_names().get(alpha_2.upper(), ())
    if not zone_names:
        return None
    try:
        return zone_names[0] if _keep_the_same_time(zone_names) else None
    except zoneinfo.ZoneInfoNotFoundError:
        return None
