from django.utils import timezone

from common.tests import IsStaffModelAdminTestCaseBase, setup_basic_project, DEFAULT_COLUMNSET_PK, DEFAULT_FIXTURES
from projects.models import ProjectColumnSet, KippoProject

from ..models import KippoProject, KippoMilestone
from ..admin import KippoProjectAdmin, KippoMilestoneAdmin


class IsStaffOrganizationKippoProjectAdminTestCase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        self.current_date = timezone.now().date()

        # create projects from 2 orgs
        self.project1 = KippoProject.objects.create(
            organization=self.organization,
            name='project1',
            category='testing',
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone1 = KippoMilestone.objects.create(
            project=self.project1,
            title='milestone1',
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.project2 = KippoProject.objects.create(
            organization=self.other_organization,
            name='project2',
            category='testing',
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone2 = KippoMilestone.objects.create(
            project=self.project2,
            title='milestone2',
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

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
        self.assertTrue(
            len(queryset_results) == expected_count,
            f'actual({len(queryset_results)}) != expected({expected_count})'
        )
        for result in queryset_results:
            self.assertTrue(result.organization == self.organization)


class IsStaffOrganizationKippoMilestoneAdminTestCase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        self.current_date = timezone.now().date()

        # create projects from 2 orgs
        self.project1 = KippoProject.objects.create(
            organization=self.organization,
            name='project1',
            category='testing',
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone1 = KippoMilestone.objects.create(
            project=self.project1,
            title='milestone1',
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        self.project2 = KippoProject.objects.create(
            organization=self.other_organization,
            name='project2',
            category='testing',
            columnset=columnset,
            start_date=self.current_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.milestone2 = KippoMilestone.objects.create(
            project=self.project2,
            title='milestone2',
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
        self.assertTrue(
            len(queryset_results) == expected_count,
            f'actual({len(queryset_results)}) != expected({expected_count})'
        )
        for result in queryset_results:
            self.assertTrue(result.project.organization == self.organization)

