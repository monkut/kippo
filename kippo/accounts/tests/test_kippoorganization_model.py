from django.test import TestCase

from ..models import KippoOrganization, KippoUser, OrganizationMembership


class KippoOrganizationTestCase(TestCase):
    fixtures = [
        "default_columnset",
        "required_bot_users",
    ]

    def setUp(self):
        self.github_manager_user = KippoUser.objects.get(username="github-manager")

    def test_timezone_defaults_to_jst(self):
        organization = KippoOrganization.objects.create(
            name="tz-default-org",
            github_organization_name="ghtzdefault",
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
        )
        self.assertEqual(organization.timezone_name, "Asia/Tokyo")

    def test_validate_timezone(self):
        from django.core.exceptions import ValidationError

        from ..models import validate_timezone

        validate_timezone("Asia/Tokyo")  # valid IANA name → no error
        with self.assertRaises(ValidationError):
            validate_timezone("Not/AZone")

    def test_current_fiscal_year_start_uses_organization_timezone(self):
        import datetime as dt
        from unittest.mock import patch

        organization = KippoOrganization.objects.create(
            name="tz-fy-org",
            github_organization_name="ghtzfy",
            fiscalyear_start_month=1,
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
        )
        # An instant that is 2026-01-01 in Tokyo (+09:00) but still 2025-12-31 in Los Angeles (-08:00).
        instant = dt.datetime(2026, 1, 1, 2, 0, tzinfo=dt.UTC)
        with patch("django.utils.timezone.now", return_value=instant):
            organization.timezone_name = "Asia/Tokyo"
            self.assertEqual(organization.current_fiscal_year_start(), dt.date(2026, 1, 1))
            organization.timezone_name = "America/Los_Angeles"
            self.assertEqual(organization.current_fiscal_year_start(), dt.date(2025, 1, 1))

    def test_current_fiscal_year_start_falls_back_to_jst_on_invalid_timezone(self):
        # validate_timezone runs only on full_clean, not .save(); a bad value must not raise.
        import datetime as dt
        from unittest.mock import patch

        organization = KippoOrganization.objects.create(
            name="tz-bad-fy-org",
            github_organization_name="ghtzbadfy",
            fiscalyear_start_month=1,
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
        )
        instant = dt.datetime(2026, 1, 1, 2, 0, tzinfo=dt.UTC)  # 2026-01-01 in JST
        with patch("django.utils.timezone.now", return_value=instant):
            for bad in ("", "Not/AZone"):
                organization.timezone_name = bad
                self.assertEqual(organization.current_fiscal_year_start(), dt.date(2026, 1, 1))

    def create_organization_unassigned_kippouser(self):
        org_name = "testorg1"
        dummy_organization = KippoOrganization(
            name=org_name,
            github_organization_name="ghdummyorg",
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
        )
        dummy_organization.save()

        expected_github_unassigned_username = f"github-unassigned-{org_name}"
        actual_candidates = KippoUser.objects.filter(username=expected_github_unassigned_username)
        self.assertTrue(actual_candidates)
        actual = actual_candidates[0]
        self.assertTrue(actual.username == expected_github_unassigned_username)

        # check for organization membership
        candidate_memberships = OrganizationMembership.objects.filter(
            organization=dummy_organization,
            user=actual,
        )
        self.assertTrue(candidate_memberships)
        self.assertTrue(len(candidate_memberships) == 1)

        unassigned_user = dummy_organization.get_unassigned_kippouser()
        self.assertTrue(unassigned_user)
