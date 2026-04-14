from django.conf import settings
from django.test import TestCase

from commons.admin import KippoAdminSite


class KippoAdminSiteTestCase(TestCase):
    def test_site_url_points_to_weekly_effort(self):
        expected = f"{settings.URL_PREFIX}/ui/weekly-effort"
        self.assertEqual(KippoAdminSite.site_url, expected)
