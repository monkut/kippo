from django.utils import timezone
from django.test import TestCase

from accounts.models import KippoOrganization, KippoUser, EmailDomain, OrganizationMembership
from tasks.models import KippoTask, KippoTaskStatus

from ..models import KippoProject, ProjectColumnSet
from ..charts.functions import get_project_weekly_effort


class ProjectsChartFunctionsTestCase(TestCase):
    fixtures = [
        'required_bot_users',
        'default_columnset',
        'default_labelset',
    ]

    def setUp(self):
        self.cli_manager = KippoUser.objects.get(username='cli-manager')

        self.organization = KippoOrganization(
            name='some org',
            github_organization_name='some-org',
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        self.organization.save()
        self.domain = 'kippo.org'
        self.emaildomain = EmailDomain(organization=self.organization,
                                       domain=self.domain,
                                       is_staff_domain=True,
                                       created_by=self.cli_manager,
                                       updated_by=self.cli_manager,
                                       )
        self.emaildomain.save()

        self.user1 = KippoUser(
            username='user1',
            github_login='user1',
            password='test',
            email='user1@github.com',
            is_staff=True,
        )
        self.user1.save()
        self.user1_membership = OrganizationMembership(
            organization=self.organization,
            is_developer=True,
            email=f'otheruser@{self.domain}',
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        self.user1_membership.save()
        self.user1.memberships.add(self.user1_membership)

        self.user2 = KippoUser(
            username='user2',
            github_login='user2',
            password='test',
            email='user2@github.com',
            is_staff=True,
        )
        self.user2.save()
        self.user2_membership = OrganizationMembership(
            organization=self.organization,
            is_developer=True,
            email=f'otheruser@{self.domain}',
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        self.user2_membership.save()
        self.user2.memberships.add(self.user2_membership)

        columnset = ProjectColumnSet.objects.get(pk=1)
        self.kippoproject = KippoProject(
            name='testproject',
            organization=self.organization,
            start_date=timezone.datetime(2019, 6, 3).date(),
            columnset=columnset,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        self.kippoproject.save()
        active_states = self.kippoproject.columnset.get_active_column_names()
        active_state = active_states[0]

        task1 = KippoTask(
            title='task1',
            category='cat1',
            project=self.kippoproject,
            assignee=self.user1,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        task1.save()
        task1status = KippoTaskStatus(
            task=task1,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=2,
            state=active_state,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        task1status.save()

        task2 = KippoTask(
            title='task2',
            category='cat2',
            project=self.kippoproject,
            assignee=self.user1,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        task2.save()
        task2status = KippoTaskStatus(
            task=task2,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=1,
            state=active_state,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        task2status.save()
        self.user1effort_total = task1status.estimate_days + task2status.estimate_days

        task3 = KippoTask(
            title='task3',
            category='cat3',
            project=self.kippoproject,
            assignee=self.user2,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        task3.save()
        task3status = KippoTaskStatus(
            task=task3,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=5,
            state=active_state,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        task3status.save()

        task4 = KippoTask(
            title='task4',
            category='cat4',
            project=self.kippoproject,
            assignee=self.user2,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        task4.save()
        task4status = KippoTaskStatus(
            task=task4,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=5,
            state=active_state,
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        task4status.save()
        self.user2effort_total = task3status.estimate_days + task4status.estimate_days

    def test_get_project_weekly_effort(self):
        wednesday_weekday = 3
        status_entries, search_dates = get_project_weekly_effort(
            project=self.kippoproject,
            current_date=timezone.datetime(2019, 6, 5).date(),
            representative_day=wednesday_weekday
        )
        self.assertTrue(status_entries)
        self.assertTrue(search_dates)
        user_status = {}
        for entry in status_entries:
            user = entry['task__assignee__github_login']
            user_status[user] = {
                'task_count': entry['task_count'],
                'estimate_days_sum': entry['estimate_days_sum']
            }
        self.assertTrue(user_status['user1']['task_count'] == 2)
        self.assertTrue(user_status['user1']['estimate_days_sum'] == self.user1effort_total)

        self.assertTrue(user_status['user2']['task_count'] == 2)
        self.assertTrue(user_status['user2']['estimate_days_sum'] == self.user2effort_total)
