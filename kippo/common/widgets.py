import datetime
import re

from django.forms.widgets import Select, Widget
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext_lazy as _

__all__ = ("MonthYearWidget",)

RE_DATE = re.compile(r"(\d{4})-(\d\d?)-(\d\d?)$")


class MonthYearWidget(Widget):
    none_value = (0, "---")
    yearmonth_field = "%s_yearmonth"

    def __init__(self, attrs=None, years=None, required=True):
        # years is an optional list/tuple of years to use in the "year" select box.
        self.attrs = attrs or {}
        self.required = required
        if years:
            self.years = years
        else:
            this_year = datetime.date.today().year
            self.years = range(this_year - 1, this_year + 1)

    def render(self, name, value, attrs=None, renderer=None):
        now = timezone.now()
        try:
            year_val, month_val = value.year, value.month
        except AttributeError:
            year_val = month_val = None
            if isinstance(value, str):
                match = RE_DATE.match(value)
                if match:
                    year_val, month_val, day_val = [int(v) for v in match.groups()]
        if not year_val and not month_val:
            year_val = now.year
            month_val = now.month
        current_value = f"{year_val}-{month_val}"

        choices = []
        for year in self.years:
            for month in range(1, 13, 1):
                choices.append((f"{year}-{month}", _(f"{year}年{month}月")))

        local_attrs = self.build_attrs(base_attrs=self.attrs)
        s = Select(choices=choices)
        select_html = s.render(self.yearmonth_field % name, current_value, local_attrs)
        return mark_safe(select_html)

    def value_from_datadict(self, data, files, name):
        year_month = data.get(self.yearmonth_field % name)
        y, m = year_month.split("-")
        if y == m == "0":
            return None
        if y and m:
            return "%s-%s-%s" % (y, m, 1)
        return data.get(name, None)
