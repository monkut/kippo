from unittest.mock import patch

from commons.tests import IsStaffModelAdminTestCaseBase
from octocat.models import GithubAccessToken

from ..admin import KippoOrganizationAdmin, KippoOrganizationAdminForm, KippoUserAdmin, OrganizationMembershipAdmin, PersonalHolidayAdmin
from ..models import KippoOrganization, KippoUser, OrganizationMembership, PersonalHoliday


class IsStaffOrganizationKippoUserModelAdminTestCase(IsStaffModelAdminTestCaseBase):
    def test_users_list_objects(self):
        modeladmin = KippoUserAdmin(KippoUser, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)

        # should list all users
        all_users_count = KippoUser.objects.count()
        self.assertTrue(all_users_count == len(qs))

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset_users = list(qs)
        expected_user_count = len(
            {m.user.id for m in OrganizationMembership.objects.filter(organization__in=self.staff_user_request.user.organizations)}
        )
        self.assertTrue(
            len(queryset_users) == expected_user_count,
            f"actual({len(queryset_users)}) != expected({expected_user_count}): {', '.join(u.username for u in queryset_users)}",
        )

        staff_user_orgids = {o.id for o in self.staff_user_request.user.organizations}
        for queryset_user in queryset_users:
            queryset_user_orgids = {o.id for o in queryset_user.organizations}
            self.assertTrue(staff_user_orgids.intersection(queryset_user_orgids))

    def test_kippoorganization_list_objects(self):
        modeladmin = KippoOrganizationAdmin(KippoOrganization, self.site)
        qs = list(modeladmin.get_queryset(self.super_user_request))
        # should list all users
        expected = KippoOrganization.objects.count()
        assert expected > 1
        actual = len(qs)
        self.assertTrue(
            actual == expected,
            f"actual({actual})[{', '.join(o.name for o in qs)}] != expected({expected})"
            f"[{', '.join(o.name for o in KippoOrganization.objects.all())}]",
        )

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset_orgs = list(qs)
        expected_org_count = len(
            {m.organization.id for m in OrganizationMembership.objects.filter(organization__in=self.staff_user_request.user.organizations)}
        )
        self.assertTrue(
            len(queryset_orgs) == expected_org_count,
            f"actual({len(queryset_orgs)}) != expected({expected_org_count}): {', '.join(o.name for o in queryset_orgs)}",
        )

        staff_user_orgids = {o.id for o in self.staff_user_request.user.organizations}
        for queryset_org in queryset_orgs:
            self.assertTrue(queryset_org.id in staff_user_orgids)

    def test_organizationmemberships_list_objects(self):
        modeladmin = OrganizationMembershipAdmin(OrganizationMembership, self.site)
        qs = list(modeladmin.get_queryset(self.super_user_request))
        # should list all
        expected = OrganizationMembership.objects.count()
        assert expected > 1
        actual = len(qs)
        msg = f"actual({actual}) != expected({expected})"
        self.assertTrue(actual == expected, msg)

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset = list(qs)
        expected_count = OrganizationMembership.objects.filter(organization__in=self.staff_user_request.user.organizations).count()
        self.assertTrue(len(queryset) == expected_count, f"actual({len(queryset)}) != expected({expected_count}): {queryset}")

        staff_user_orgids = {o.id for o in self.staff_user_request.user.organizations}
        for membership in queryset:
            self.assertTrue(membership.organization.id in staff_user_orgids)

    def test_personalholidays_list_objects(self):
        modeladmin = PersonalHolidayAdmin(PersonalHoliday, self.site)
        qs = list(modeladmin.get_queryset(self.super_user_request))
        # should list all
        expected = PersonalHoliday.objects.count()
        assert expected > 1
        actual = len(qs)
        msg = f"actual({actual}) != expected({expected})"
        self.assertTrue(actual == expected, msg)

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset = list(qs)
        expected_count = (
            PersonalHoliday.objects.filter(user__organizationmembership__organization__in=self.staff_user_request.user.organizations)
            .distinct()
            .count()
        )
        self.assertTrue(len(queryset) == expected_count, f"actual({len(queryset)}) != expected({expected_count}): {queryset}")

        staff_user_orgids = {o.id for o in self.staff_user_request.user.organizations}
        for personalholiday in queryset:
            self.assertTrue(set(o.id for o in personalholiday.user.organizations).intersection(staff_user_orgids))


class KippoOrganizationAdminFormTestCase(IsStaffModelAdminTestCaseBase):
    """Test the KippoOrganizationAdminForm dynamic template choices."""

    def test_form_shows_templates_when_token_exists(self):
        """Form should show GitHub project templates when organization has a token."""
        GithubAccessToken.objects.create(
            organization=self.organization,
            token="test-token",  # noqa: S106
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        mock_projects = [
            {"id": "PVT_kwDOtest1", "title": "Template 1", "url": "https://github.com/orgs/test/projects/1", "number": 1, "template": True},
            {"id": "PVT_kwDOtest2", "title": "Template 2", "url": "https://github.com/orgs/test/projects/2", "number": 2, "template": True},
        ]

        with patch("accounts.admin.get_organization_projects_v2", return_value=mock_projects):
            form = KippoOrganizationAdminForm(instance=self.organization)

        choices = form.fields["default_github_project_template"].choices
        self.assertEqual(len(choices), 3)  # empty + 2 templates
        self.assertEqual(choices[0][0], "")  # First is empty option
        self.assertEqual(choices[1][0], "PVT_kwDOtest1")
        self.assertEqual(choices[1][1], "Template 1 (PVT_kwDOtest1)")
        self.assertEqual(choices[2][0], "PVT_kwDOtest2")
        self.assertEqual(choices[2][1], "Template 2 (PVT_kwDOtest2)")

    def test_form_shows_only_empty_choice_without_token(self):
        """Form should show only the empty choice when organization has no token."""
        form = KippoOrganizationAdminForm(instance=self.organization)

        choices = form.fields["default_github_project_template"].choices
        self.assertEqual(len(choices), 1)
        self.assertEqual(choices[0][0], "")

    def test_form_shows_only_empty_choice_for_new_organization(self):
        """Form should show only the empty choice for a new (unsaved) organization."""
        form = KippoOrganizationAdminForm()

        choices = form.fields["default_github_project_template"].choices
        self.assertEqual(len(choices), 1)
        self.assertEqual(choices[0][0], "")

    def test_form_handles_api_error_gracefully(self):
        """Form should show only empty choice when GitHub API fails."""
        GithubAccessToken.objects.create(
            organization=self.organization,
            token="test-token",  # noqa: S106
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        with patch("accounts.admin.get_organization_projects_v2", side_effect=Exception("API Error")):
            form = KippoOrganizationAdminForm(instance=self.organization)

        choices = form.fields["default_github_project_template"].choices
        self.assertEqual(len(choices), 1)
        self.assertEqual(choices[0][0], "")

    def test_form_saves_selected_template_id(self):
        """Form should save the selected template node ID."""
        GithubAccessToken.objects.create(
            organization=self.organization,
            token="test-token",  # noqa: S106
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        mock_projects = [
            {"id": "PVT_kwDOtest1", "title": "Template 1", "url": "https://github.com/orgs/test/projects/1", "number": 1, "template": True},
        ]

        with patch("accounts.admin.get_organization_projects_v2", return_value=mock_projects):
            form = KippoOrganizationAdminForm(
                instance=self.organization,
                data={
                    "name": self.organization.name,
                    "github_organization_name": self.organization.github_organization_name,
                    "day_workhours": self.organization.day_workhours,
                    "default_task_display_state": self.organization.default_task_display_state,
                    "weekly_project_time_deadline": self.organization.weekly_project_time_deadline,
                    "weekly_effort_close_offset_days": self.organization.weekly_effort_close_offset_days,
                    "slack_command_name": self.organization.slack_command_name,
                    "fiscalyear_start_month": self.organization.fiscalyear_start_month,
                    "timezone": self.organization.timezone,
                    "project_assignment_member_soft_ceiling": self.organization.project_assignment_member_soft_ceiling,
                    "default_github_project_template": "PVT_kwDOtest1",
                },
            )
            self.assertTrue(form.is_valid(), form.errors)
            org = form.save()

        self.assertEqual(org.default_github_project_template, "PVT_kwDOtest1")
