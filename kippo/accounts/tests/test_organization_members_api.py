"""Tests for the org-level user-listing API (kippo#14)."""

import datetime
import uuid
from http import HTTPStatus

from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Country, EmailDomain, KippoOrganization, KippoUser, OrganizationMembership, PersonalHoliday, PublicHoliday


class OrganizationListTestCase(TestCase):
    """`GET /api/organizations/` — list orgs the requester belongs to."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.user = created["KippoUser"]
        self.organization = created["KippoOrganization"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # Second org the user does NOT belong to.
        self.other_organization = KippoOrganization.objects.create(
            name="other-org",
            github_organization_name="other-org",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.client = APIClient()

    def test_unauthenticated_returns_401(self):
        url = f"{settings.URL_PREFIX}/api/organizations/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)

    def test_authenticated_user_sees_only_own_orgs(self):
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/organizations/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertIn("organizations", data)
        org_ids = [o["id"] for o in data["organizations"]]
        self.assertIn(str(self.organization.id), org_ids)
        self.assertNotIn(str(self.other_organization.id), org_ids)

    def test_user_without_memberships_gets_empty_list(self):
        loner = KippoUser.objects.create(username="loner", is_staff=True)
        self.client.force_authenticate(user=loner)
        url = f"{settings.URL_PREFIX}/api/organizations/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.json(), {"organizations": []})

    def test_superuser_sees_all_orgs(self):
        superuser = KippoUser.objects.create(username="root", is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=superuser)
        url = f"{settings.URL_PREFIX}/api/organizations/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        org_ids = [o["id"] for o in response.json()["organizations"]]
        self.assertIn(str(self.organization.id), org_ids)
        self.assertIn(str(self.other_organization.id), org_ids)

    def test_org_response_shape(self):
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/organizations/"
        response = self.client.get(url)
        org = next(o for o in response.json()["organizations"] if o["id"] == str(self.organization.id))
        self.assertEqual(set(org.keys()), {"id", "name", "github_organization_name"})
        self.assertEqual(org["name"], self.organization.name)
        self.assertEqual(org["github_organization_name"], self.organization.github_organization_name)

    def test_retrieve_own_org_returns_200(self):
        """`GET /api/organizations/<id>/` returns the org when the requester is a member."""
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/organizations/{self.organization.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.json()["id"], str(self.organization.id))

    def test_retrieve_other_org_returns_404(self):
        """`GET /api/organizations/<id>/` is filtered through get_queryset, so a non-member gets
        404 (org is filtered out). This is the cross-org leak guard for the retrieve action.
        """
        self.client.force_authenticate(user=self.user)
        url = f"{settings.URL_PREFIX}/api/organizations/{self.other_organization.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_loner_cannot_retrieve_any_org(self):
        """A user with zero memberships gets 404 for every existing org id."""
        loner = KippoUser.objects.create(username="loner2", is_staff=True)
        self.client.force_authenticate(user=loner)
        for org_id in (self.organization.id, self.other_organization.id):
            url = f"{settings.URL_PREFIX}/api/organizations/{org_id}/"
            response = self.client.get(url)
            self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND, f"leaked org {org_id}")


class OrganizationMembersAPITestCase(TestCase):
    """`GET /api/organizations/<id>/members/` — list members of one org."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.user = created["KippoUser"]
        self.organization = created["KippoOrganization"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # PM member, with all the per-org PII populated.
        self.pm_user = KippoUser.objects.create(
            username="pm-user",
            first_name="Pat",
            last_name="Manager",
            github_login="pat-pm",
            is_staff=True,
        )
        OrganizationMembership.objects.create(
            user=self.pm_user,
            organization=self.organization,
            email="pat@github.com",
            slack_username="pat",
            slack_user_id="U02PM",
            slack_image_url="https://example.com/pat.png",
            is_developer=False,
            is_project_manager=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        # Inactive developer. NOTE: OrganizationMembership.save() re-activates the user when
        # the org has an is_staff_domain — so we flip is_active=False AFTER the membership is created.
        self.inactive_user = KippoUser.objects.create(
            username="ghost",
            github_login="ghost",
            is_staff=False,
        )
        OrganizationMembership.objects.create(
            user=self.inactive_user,
            organization=self.organization,
            is_developer=True,
            is_project_manager=False,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.inactive_user.is_active = False
        self.inactive_user.save()

        # Second org the requester does NOT belong to.
        self.other_organization = KippoOrganization.objects.create(
            name="other-org",
            github_organization_name="other-org",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        EmailDomain.objects.create(
            organization=self.other_organization,
            domain="example.com",
            is_staff_domain=False,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_org_user = KippoUser.objects.create(username="otheruser", is_staff=True)
        OrganizationMembership.objects.create(
            user=self.other_org_user,
            organization=self.other_organization,
            is_developer=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.client = APIClient()

    def _url(self, org_id: str | uuid.UUID) -> str:
        return f"{settings.URL_PREFIX}/api/organizations/{org_id}/members/"

    def test_unauthenticated_returns_401(self):
        response = self.client.get(self._url(self.organization.id))
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)

    def test_nonexistent_org_returns_404(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(uuid.uuid4()))
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_non_member_returns_403(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(self.other_organization.id))
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

    def test_member_can_list_org_members(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(self.organization.id))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertIn("members", data)
        usernames = [m["username"] for m in data["members"]]
        self.assertIn(self.user.username, usernames)
        self.assertIn(self.pm_user.username, usernames)

    def test_inactive_user_excluded_by_default(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(self.organization.id))
        usernames = [m["username"] for m in response.json()["members"]]
        self.assertNotIn(self.inactive_user.username, usernames)

    def test_include_inactive_opts_in(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(self.organization.id) + "?include_inactive=true")
        usernames = [m["username"] for m in response.json()["members"]]
        self.assertIn(self.inactive_user.username, usernames)

    def test_filter_is_developer(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(self.organization.id) + "?is_developer=true")
        usernames = [m["username"] for m in response.json()["members"]]
        # octocat (setup_basic_project) is is_developer=True; pm-user is is_developer=False.
        self.assertIn(self.user.username, usernames)
        self.assertNotIn(self.pm_user.username, usernames)

    def test_filter_is_project_manager(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(self.organization.id) + "?is_project_manager=true")
        usernames = [m["username"] for m in response.json()["members"]]
        self.assertEqual(usernames, [self.pm_user.username])

    def test_unassigned_bot_excluded(self):
        # KippoOrganization.save() auto-creates an `unassigned-<slug>` membership.
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(self.organization.id))
        for member in response.json()["members"]:
            self.assertFalse(
                member["username"].startswith(settings.UNASSIGNED_USER_GITHUB_LOGIN_PREFIX),
                f"unassigned bot leaked into members listing: {member['username']}",
            )

    def test_response_shape_has_all_fields(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(self.organization.id))
        pm = next(m for m in response.json()["members"] if m["username"] == self.pm_user.username)
        self.assertEqual(
            set(pm.keys()),
            {
                "user_id",
                "username",
                "display_name",
                "first_name",
                "last_name",
                "email",
                "github_login",
                "is_developer",
                "is_project_manager",
                "slack_username",
                "slack_user_id",
                "slack_image_url",
                "available_work_days",
            },
        )
        # `available_work_days` is null when the `month` query parameter is not given.
        self.assertIsNone(pm["available_work_days"])
        # Slack/email fields come from OrganizationMembership, not KippoUser.
        self.assertEqual(pm["email"], "pat@github.com")
        self.assertEqual(pm["slack_username"], "pat")
        self.assertEqual(pm["slack_user_id"], "U02PM")
        self.assertEqual(pm["slack_image_url"], "https://example.com/pat.png")
        self.assertEqual(pm["first_name"], "Pat")
        self.assertEqual(pm["last_name"], "Manager")
        self.assertFalse(pm["is_developer"])
        self.assertTrue(pm["is_project_manager"])

    def test_superuser_can_list_any_org(self):
        superuser = KippoUser.objects.create(username="root", is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=superuser)
        response = self.client.get(self._url(self.other_organization.id))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        usernames = [m["username"] for m in response.json()["members"]]
        self.assertIn(self.other_org_user.username, usernames)


class OrganizationMembersMonthAvailableWorkdaysTestCase(TestCase):
    """`?month=YYYY-MM-DD` populates per-member `available_work_days`."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.user = created["KippoUser"]
        self.organization = created["KippoOrganization"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # 2026-06: 22 weekdays (1-Mon … 30-Tue). No JP public holidays in June.
        self.country = Country.objects.create(name="Japan")

        # User with full Mon-Fri commitment, JP holidays, one personal holiday.
        self.fulltime_user = KippoUser.objects.create(
            username="ft-user",
            first_name="Full",
            last_name="Time",
            holiday_country=self.country,
            is_staff=True,
        )
        OrganizationMembership.objects.create(
            user=self.fulltime_user,
            organization=self.organization,
            email="ft@github.com",
            monday=True,
            tuesday=True,
            wednesday=True,
            thursday=True,
            friday=True,
            saturday=False,
            sunday=False,
            is_developer=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        # Personal holiday: 2026-06-15 (Mon), 3 days → 15, 16, 17.
        PersonalHoliday.objects.create(user=self.fulltime_user, day=datetime.date(2026, 6, 15), duration=3, is_half=False)

        # Public holiday in JP: 2026-06-08 (Mon) — synthetic, real JP June has none.
        PublicHoliday.objects.create(country=self.country, name="Synthetic Holiday", day=datetime.date(2026, 6, 8))

        # Part-time user: Mon/Wed/Fri only, no holiday country (skips public holidays).
        self.parttime_user = KippoUser.objects.create(
            username="pt-user",
            first_name="Part",
            last_name="Time",
            is_staff=True,
        )
        OrganizationMembership.objects.create(
            user=self.parttime_user,
            organization=self.organization,
            email="pt@github.com",
            monday=True,
            tuesday=False,
            wednesday=True,
            thursday=False,
            friday=True,
            saturday=False,
            sunday=False,
            is_developer=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _url(self, **params) -> str:
        url = f"{settings.URL_PREFIX}/api/organizations/{self.organization.id}/members/"
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return url

    def _member(self, response_json: dict, username: str) -> dict:
        return next(m for m in response_json["members"] if m["username"] == username)

    def test_no_month_param_returns_null_available_work_days(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, HTTPStatus.OK)
        ft = self._member(response.json(), "ft-user")
        self.assertIsNone(ft["available_work_days"])

    def test_month_param_populates_available_work_days(self):
        response = self.client.get(self._url(month="2026-06-01"))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        ft = self._member(response.json(), "ft-user")
        # June 2026: 22 weekdays minus 1 public holiday (06-08) minus 3 personal (06-15..17) = 18.
        self.assertEqual(ft["available_work_days"], 18)

    def test_parttime_user_only_committed_weekdays_counted(self):
        response = self.client.get(self._url(month="2026-06-01"))
        pt = self._member(response.json(), "pt-user")
        # Mon/Wed/Fri only in June 2026:
        # Mon: 1, 8, 15, 22, 29 = 5
        # Wed: 3, 10, 17, 24 = 4
        # Fri: 5, 12, 19, 26 = 4
        # Total = 13 (no holiday subtraction; pt-user has no holiday_country and no PersonalHoliday).
        self.assertEqual(pt["available_work_days"], 13)

    def test_month_param_with_arbitrary_day_snaps_to_month_start(self):
        # Any day-of-month should yield the same count as day=01.
        response_mid = self.client.get(self._url(month="2026-06-17"))
        response_first = self.client.get(self._url(month="2026-06-01"))
        ft_mid = self._member(response_mid.json(), "ft-user")
        ft_first = self._member(response_first.json(), "ft-user")
        self.assertEqual(ft_mid["available_work_days"], ft_first["available_work_days"])

    def test_invalid_month_param_returns_400(self):
        response = self.client.get(self._url(month="not-a-date"))
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)

    def test_month_param_other_month_unaffected_by_june_holidays(self):
        # July 2026: 23 weekdays, no JP public holidays declared, no personal holidays for ft-user → 23.
        response = self.client.get(self._url(month="2026-07-01"))
        ft = self._member(response.json(), "ft-user")
        self.assertEqual(ft["available_work_days"], 23)
