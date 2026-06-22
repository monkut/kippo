from django.conf import settings
from django.contrib import admin
from django.test import RequestFactory, TestCase

from commons.admin import KippoAdminSite

from . import IsStaffModelAdminTestCaseBase


class KippoAdminSiteTestCase(TestCase):
    def test_site_url_points_to_weekly_effort(self):
        expected = f"{settings.URL_PREFIX}/ui/weekly-effort"
        self.assertEqual(KippoAdminSite.site_url, expected)


class KippoAdminSiteAppOrderTestCase(IsStaffModelAdminTestCaseBase):
    def setUp(self):
        super().setUp()
        self.request = RequestFactory().get("/admin/")
        self.request.user = self.superuser_no_org

    def test_get_app_list_pins_customers_then_projects(self):
        app_list = admin.site.get_app_list(self.request)
        app_labels = [app["app_label"] for app in app_list]
        self.assertEqual(app_labels[:2], ["customers", "projects"])

    def test_get_app_list_preserves_default_order_for_remaining_apps(self):
        # the relative order of non-pinned apps must match Django's default ordering
        default_list = super(KippoAdminSite, admin.site).get_app_list(self.request)
        default_remaining = [app["app_label"] for app in default_list if app["app_label"] not in KippoAdminSite.APP_PRIORITY]
        actual_remaining = [app["app_label"] for app in admin.site.get_app_list(self.request) if app["app_label"] not in KippoAdminSite.APP_PRIORITY]
        self.assertEqual(actual_remaining, default_remaining)
