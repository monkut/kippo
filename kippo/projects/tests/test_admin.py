from http import HTTPStatus
from unittest.mock import MagicMock

from accounts.models import KippoUser, OrganizationMembership
from common.tests import DEFAULT_COLUMNSET_PK, DEFAULT_FIXTURES, IsStaffModelAdminTestCaseBase, setup_basic_project
from django.urls import reverse
from django.utils import timezone
from projects.models import KippoProject, ProjectColumnSet

from ..admin import KippoMilestoneAdmin, KippoProjectAdmin, ProjectWeeklyEffortAdminInline
from ..models import KippoMilestone, KippoProject


class MockRequest:
    GET = {}
    POST = {}
    path = ""
    _messages = MagicMock()

    def __init__(self, *args, **kwargs):
        self.GET = {}
        self.POST = {}
        self._messages = MagicMock()

    def get_full_path(self):
        return self.path


class IsStaffOrganizationKippoProjectAdminTestCase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        self.current_date = timezone.now().date()

        # create projects from 2 orgs
        self.project1 = KippoProject.objects.create(
            organization=self.organization,
            name="project1",
            category="testing",
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone1 = KippoMilestone.objects.create(
            project=self.project1,
            title="milestone1",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.project2 = KippoProject.objects.create(
            organization=self.other_organization,
            name="project2",
            category="testing",
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone2 = KippoMilestone.objects.create(
            project=self.project2,
            title="milestone2",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.organization_usera = KippoUser.objects.create(username="organization_usera")
        organization_usera_membership = OrganizationMembership.objects.create(organization=self.organization, user=self.organization_usera)
        self.organization_users = OrganizationMembership.objects.filter(organization=self.organization).values_list("user", flat=True)

        other_organization_usera = KippoUser.objects.create(username="other_organization_usera")
        other_organization_usera_membership = OrganizationMembership.objects.create(organization=self.organization, user=other_organization_usera)

    def test_list_objects(self):
        modeladmin = KippoProjectAdmin(KippoProject, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)

        # superuser should list all tasks
        all_tasks_count = KippoProject.objects.count()
        self.assertTrue(all_tasks_count == len(qs))

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset_results = list(qs)
        expected_count = KippoProject.objects.filter(organization__in=self.staff_user_request.user.organizations).count()
        self.assertTrue(len(queryset_results) == expected_count, f"actual({len(queryset_results)}) != expected({expected_count})")
        for result in queryset_results:
            self.assertTrue(result.organization == self.organization)

    def test_projectweeklyeffort_inlineadmin(self):
        assert KippoUser.objects.all().count() > self.organization_users.count()

        # modeladmin = KippoProjectAdmin(KippoProject, self.site)
        orguser_request = MockRequest()
        orguser_request.user = self.organization_usera
        inline = ProjectWeeklyEffortAdminInline(parent_model=KippoProject, admin_site=self.site)
        formset = inline.get_formset(request=orguser_request, obj=self.project1)
        # check project form users
        # -- compare user ids
        expected = set(self.organization_users)
        actual = set(u.id for u in formset.form.base_fields["user"].queryset)
        self.assertEqual(actual, expected)


class IsStaffOrganizationKippoMilestoneAdminTestCase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        self.current_date = timezone.now().date()

        # create projects from 2 orgs
        self.project1 = KippoProject.objects.create(
            organization=self.organization,
            name="project1",
            category="testing",
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone1 = KippoMilestone.objects.create(
            project=self.project1,
            title="milestone1",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.project2 = KippoProject.objects.create(
            organization=self.other_organization,
            name="project2",
            category="testing",
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone2 = KippoMilestone.objects.create(
            project=self.project2,
            title="milestone2",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def test_list_objects(self):
        modeladmin = KippoMilestoneAdmin(KippoMilestone, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)

        # superuser should list all tasks
        all_tasks_count = KippoProject.objects.count()
        self.assertTrue(all_tasks_count == len(qs))

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset_results = list(qs)
        expected_count = KippoProject.objects.filter(organization__in=self.staff_user_request.user.organizations).count()
        self.assertTrue(len(queryset_results) == expected_count, f"actual({len(queryset_results)}) != expected({expected_count})")
        for result in queryset_results:
            self.assertTrue(result.project.organization == self.organization)


class ProjectsAdminViewTestCase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        self.current_date = timezone.now().date()

        # create projects from 2 orgs
        self.project1 = KippoProject.objects.create(
            organization=self.organization,
            name="project1",
            category="testing",
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone1 = KippoMilestone.objects.create(
            project=self.project1,
            title="milestone1",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.project2 = KippoProject.objects.create(
            organization=self.other_organization,
            name="project2",
            category="testing",
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone2 = KippoMilestone.objects.create(
            project=self.project2,
            title="milestone2",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def test_kippomilestone_view(self):

        url = reverse("admin:projects_kippomilestone_changelist")
        self.client.force_login(self.superuser_no_org)
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertTemplateUsed(response, "admin/change_list.html")

        # self.client.force_login(self.client_user)
        # response = self.client.get(url)
        # self.assertEqual(response.status_code, HTTPStatus.OK)
        # self.assertTemplateUsed(response, "admin/change_list.html")
