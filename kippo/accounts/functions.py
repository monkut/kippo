from collections.abc import Generator

from django.utils import timezone

from .models import PersonalHoliday


def get_personal_holidays_generator(from_datetime: timezone.datetime | None) -> Generator:
    qs = PersonalHoliday.objects.all()
    if from_datetime:
        qs = qs.filter(day__gte=from_datetime.date())
    for personal_holiday in qs:
        yield from personal_holiday.get_weeklyeffort_hours()
