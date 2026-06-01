import datetime
from http import HTTPStatus

from accounts.models import KippoUser, OrganizationMembership
from commons.definitions import MONDAY
from commons.tests import DEFAULT_FIXTURES, IsStaffModelAdminTestCaseBase, setup_basic_project
from commons.tests.utils import MockRequest, reset_buckets
from django.contrib import admin
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from projects.admin import ProjectWeeklyEffortAdmin, ProjectWeeklyEffortAdminInline
from projects.models import KippoProject, ProjectWeeklyEffort


class ProjectWeeklyEffortAdminTestCase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        reset_buckets()
        super().setUp()
        created_objects = setup_basic_project(organization=self.organization)
        self.project1 = created_objects["KippoProject"]
        self.project2 = created_objects["KippoProject2"]

        self.staffuser_with_org2_username = "staffuser_with_org2"
        self.staffuser_with_org2 = KippoUser.objects.create(username=self.staffuser_with_org2_username, is_superuser=False, is_staff=True)

        # add membership
        membership = OrganizationMembership(
            user=self.staffuser_with_org2,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            is_developer=True,
        )
        membership.save()

        # create ProjectWeeklyEffortAdmin for each project
        # get mondays (week_start) from at least 3 months ago
        today = timezone.now()
        three_months_ago = timezone.now() - timezone.timedelta(days=3 * 30)
        current = three_months_ago
        while current <= today:
            if current.weekday() == MONDAY:
                for project in (self.project1, self.project2):
                    for user in (self.staffuser_with_org, self.staffuser_with_org2):
                        ProjectWeeklyEffort.objects.create(week_start=current, project=project, user=user, hours=5)
            current += timezone.timedelta(days=1)

        self.staffuser_with_org1_request = MockRequest()
        self.staffuser_with_org1_request.user = self.staffuser_with_org

        self.staffuser_with_org2_request = MockRequest()
        self.staffuser_with_org2_request.user = self.staffuser_with_org2

    def test_hours_validators_reject_negative_and_over_max(self):
        """Model validators (enforced by the admin ModelForm via full_clean) reject out-of-range hours.

        The admin add form is the only interactive create surface that previously accepted a
        negative `hours` (the UI and Slack flows already block it). full_clean() exercises the
        same MinValue/MaxValue validators the admin form runs.
        """
        today = timezone.now()
        monday = today - timezone.timedelta(days=today.weekday())

        negative = ProjectWeeklyEffort(week_start=monday, project=self.project1, user=self.staffuser_with_org, hours=-1)
        with self.assertRaises(ValidationError) as ctx:
            negative.full_clean()
        self.assertIn("hours", ctx.exception.message_dict)

        over_max = ProjectWeeklyEffort(week_start=monday, project=self.project1, user=self.staffuser_with_org, hours=169)
        with self.assertRaises(ValidationError) as ctx:
            over_max.full_clean()
        self.assertIn("hours", ctx.exception.message_dict)

        # zero is valid (consistent with the UI's `hours >= 0` create filter)
        zero = ProjectWeeklyEffort(week_start=monday, project=self.project1, user=self.staffuser_with_org, hours=0)
        zero.full_clean()  # should not raise

    def _future_monday(self, weeks_ahead: int = 2) -> datetime.date:
        """A Monday outside the setUp-created (3 months ago .. today) range to avoid unique_together clashes."""
        future = timezone.now() + timezone.timedelta(weeks=weeks_ahead)
        return (future - timezone.timedelta(days=future.weekday())).date()

    def test_inline_user_queryset_scoped_to_self_for_non_superuser(self):
        """Non-superusers can only add ProjectWeeklyEffort for themselves via the project inline."""
        inline = ProjectWeeklyEffortAdminInline(KippoProject, admin.site)
        formset = inline.get_formset(self.staffuser_with_org1_request, obj=self.project1)
        user_ids = list(formset.form.base_fields["user"].queryset.values_list("id", flat=True))
        self.assertEqual(user_ids, [self.staffuser_with_org.id])

    def test_inline_user_queryset_all_org_members_for_superuser(self):
        """Superusers may add ProjectWeeklyEffort for any member of the project's organization."""
        inline = ProjectWeeklyEffortAdminInline(KippoProject, admin.site)
        formset = inline.get_formset(self.super_user_request, obj=self.project1)
        user_ids = set(formset.form.base_fields["user"].queryset.values_list("id", flat=True))
        self.assertIn(self.staffuser_with_org.id, user_ids)
        self.assertIn(self.staffuser_with_org2.id, user_ids)

    def test_standalone_admin_forces_user_to_self_for_non_superuser(self):
        """save_model forces the entry's user to the requester for non-superusers (tamper guard)."""
        model_admin = ProjectWeeklyEffortAdmin(ProjectWeeklyEffort, admin.site)
        obj = ProjectWeeklyEffort(week_start=self._future_monday(2), project=self.project1, user=self.staffuser_with_org2, hours=5)
        model_admin.save_model(self.staffuser_with_org1_request, obj, form=None, change=False)
        obj.refresh_from_db()
        self.assertEqual(obj.user, self.staffuser_with_org)

    def test_standalone_admin_superuser_may_set_other_user(self):
        """Superusers may create effort on behalf of another user."""
        model_admin = ProjectWeeklyEffortAdmin(ProjectWeeklyEffort, admin.site)
        obj = ProjectWeeklyEffort(week_start=self._future_monday(3), project=self.project1, user=self.staffuser_with_org2, hours=6)
        model_admin.save_model(self.super_user_request, obj, form=None, change=False)
        obj.refresh_from_db()
        self.assertEqual(obj.user, self.staffuser_with_org2)

    def test_download_action(self):
        data = {
            "action": "download_csv",
            ACTION_CHECKBOX_NAME: [e.id for e in ProjectWeeklyEffort.objects.filter(project__organization__in=self.staffuser_with_org.organizations)],
        }
        change_url = reverse("admin:projects_projectweeklyeffort_changelist")
        self.client.force_login(self.staffuser_with_org)
        response = self.client.post(change_url, data, follow=True)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        expected = "/projects/download/"
        actual = response.redirect_chain[-1][0]
        self.assertTrue(actual.startswith(expected), f"actual({actual}) != expected({expected})")
