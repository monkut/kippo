import datetime

from django.test import SimpleTestCase

from commons.functions import add_months


class AddMonthsTestCase(SimpleTestCase):
    def test_shifts_forward_and_backward_across_years(self):
        self.assertEqual(add_months(datetime.date(2026, 11, 20), 6), datetime.date(2027, 5, 20))
        self.assertEqual(add_months(datetime.date(2026, 4, 11), -6), datetime.date(2025, 10, 11))

    def test_clamps_to_last_day_of_target_month(self):
        self.assertEqual(add_months(datetime.date(2026, 8, 31), 6), datetime.date(2027, 2, 28))
        self.assertEqual(add_months(datetime.date(2027, 8, 31), 6), datetime.date(2028, 2, 29))
