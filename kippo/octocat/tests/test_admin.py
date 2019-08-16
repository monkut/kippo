from django.db.models import Q
from django.utils import timezone

from common.tests import IsStaffModelAdminTestCaseBase

from projects.models import KippoProject, KippoMilestone, ProjectColumnSet
from ..models import GithubRepository, GithubMilestone, GithubRepositoryLabelSet
from ..admin import GithubRepositoryAdmin, GithubMilestoneAdmin, GithubRepositoryLabelSetAdmin


DEFAULT_COLUMNSET_PK = '414e69c8-8ea3-4c9c-8129-6f5aac108fa2'


class IsStaffOrganizationAdminTestCase(IsStaffModelAdminTestCaseBase):

    def setUp(self):
        super().setUp()
        self.current_date = timezone.now().date()
        default_columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        # add GithubRepositories
        self.repository = GithubRepository.objects.create(
            organization=self.organization,
            name='myrepo',
            api_url='https://api.github.com/1',
            html_url='https://github.com/1',
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_repository = GithubRepository.objects.create(
            organization=self.other_organization,
            name='myrepo2',
            api_url='https://api.github.com/2',
            html_url='https://github.com/2',
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        # add GithubMilestones
        # -- create project for milestones
        # -- create kippomilestones
        # create projects from 2 orgs
        self.project1 = KippoProject.objects.create(
            organization=self.organization,
            name='project1',
            category='testing',
            columnset=default_columnset,
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
            columnset=default_columnset,
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

        self.ghmilestone = GithubMilestone.objects.create(
            milestone=self.milestone1,
            repository=self.repository,
            number=123,
            api_url='https://api.github.com/milestone/1',
            html_url='https://github.com/milestone/1',
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_ghmilestone = GithubMilestone.objects.create(
            milestone=self.milestone2,
            repository=self.other_repository,
            number=321,
            api_url='https://api.github.com/milestone/3',
            html_url='https://github.com/milestone/3',
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        # add GithubLabelsets
        self.githublabelset = GithubRepositoryLabelSet.objects.create(
            organization=self.organization,
            name='mytestlabelset',
            labels=[{"name": "category:X", "description": "", "color": "AED6F1"},],
        )
        self.other_githublabelset = GithubRepositoryLabelSet.objects.create(
            organization=self.other_organization,
            name='othertestlabelset',
            labels=[{"name": "category:X", "description": "", "color": "AED6F1"},],
        )

    def test_githubrepositoryadmin_list_objects(self):
        modeladmin = GithubRepositoryAdmin(GithubRepository, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)

        # should list all
        all_count = GithubRepository.objects.count()
        self.assertTrue(all_count == len(qs))

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset = list(qs)
        expected_count = GithubRepository.objects.filter(organization__in=self.staff_user_request.user.organizations).count()
        self.assertTrue(
            len(queryset) == expected_count,
            f'actual({len(queryset)}) != expected({expected_count}): {", ".join(r.name for r in queryset)}'
        )

    def test_githubmilestoneadmin_list_objects(self):
        modeladmin = GithubMilestoneAdmin(GithubMilestone, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)

        # should list all
        all_count = GithubMilestone.objects.count()
        self.assertTrue(all_count == len(qs))

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset = list(qs)
        expected_count = GithubMilestone.objects.filter(repository__organization__in=self.staff_user_request.user.organizations).count()
        self.assertTrue(
            len(queryset) == expected_count,
            f'actual({len(queryset)}) != expected({expected_count}): {", ".join(str(m.number) for m in queryset)}'
        )

    def test_githublabelsetadmin_list_objects(self):
        modeladmin = GithubRepositoryLabelSetAdmin(GithubRepositoryLabelSet, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)

        # should list all
        all_count = GithubRepositoryLabelSet.objects.count()
        self.assertTrue(all_count == len(qs))

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset = list(qs)
        expected_count = GithubRepositoryLabelSet.objects.filter(
            Q(organization__in=self.staff_user_request.user.organizations) |
            Q(organization__isnull=True)
        ).count()
        self.assertTrue(
            len(queryset) == expected_count,
            f'actual({len(queryset)}) != expected({expected_count}): {", ".join(r.name for r in queryset)}'
        )
