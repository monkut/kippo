from django.test import TestCase
from django.utils import timezone

from accounts.models import KippoOrganization, KippoUser, EmailDomain, OrganizationMembership
from projects.models import KippoProject, ProjectColumnSet
from ..models import KippoTask, KippoTaskStatus
from ..functions import (
    get_github_issue_prefixed_labels,
    get_github_issue_estimate_label,
    get_github_issue_category_label,
    get_projects_load
)


DEFAULT_COLUMNSET_PK = '414e69c8-8ea3-4c9c-8129-6f5aac108fa2'


class LabelMock:

    def __init__(self, name, **kwargs):
        self.name = name

        # https://developer.github.com/v3/issues/labels/#get-a-single-label
        self.id = kwargs.get('id', 208045947)
        self.node_id = kwargs.get('node_id', "MDU6TGFiZWwyMDgwNDU5NDc=")
        self.url = kwargs.get('url', 'https://api.github.com/repos/octocat/Hello-World/labels/enhancement')
        self.description = kwargs.get('description', 'New Feature Default Description')
        self.color = kwargs.get('color', 'a2eeef')
        self.default = kwargs.get('default', True)


class IssueMock:

    def __init__(self, label_names: list = None):
        self.labels = []
        for label_name in label_names:
            label = LabelMock(name=label_name)
            self.labels.append(label)


class TaskGithubLabelFunctionsTestCase(TestCase):

    def test_get_github_issue_prefixed_labels(self):
        category_name = 'category:testcat'
        category_value = 'testcat'

        req_name = 'req:B01'
        req_value = 'B01'

        issue = IssueMock(
            label_names=[
                category_name,
                req_name,
            ]
        )
        prefixed_labels = get_github_issue_prefixed_labels(issue)
        expected_values = (
            category_value,
            req_value
        )
        for prefixed_label in prefixed_labels:
            assert prefixed_label.value in expected_values

    def test_get_github_issue_estimate_label_hours(self):
        prefix = 'estimate:'
        for suffix in ('h', 'hour', 'hours'):
            label_name = f'{prefix}1{suffix}'
            issue = IssueMock(
                label_names=[
                    label_name
                ]
            )
            expected_estimate = 1  # days
            actual_estimate = get_github_issue_estimate_label(issue, prefix, day_workhours=8)
            self.assertTrue(actual_estimate == expected_estimate, f'actual({actual_estimate}) != expected({expected_estimate}): {label_name}')

            label_name = f'{prefix}8{suffix}'
            issue = IssueMock(
                label_names=[
                    label_name
                ]
            )
            expected_estimate = 1  # days
            actual_estimate = get_github_issue_estimate_label(issue, prefix, day_workhours=8)
            self.assertTrue(actual_estimate == expected_estimate, f'actual({actual_estimate}) != expected({expected_estimate}): {label_name}')

            label_name = f'{prefix}15{suffix}'
            issue = IssueMock(
                label_names=[
                    label_name
                ]
            )
            expected_estimate = 2
            actual_estimate = get_github_issue_estimate_label(issue, prefix, day_workhours=8)
            self.assertTrue(actual_estimate == expected_estimate, f'actual({actual_estimate}) != expected({expected_estimate}): {label_name}')

            label_name = f'{prefix}16{suffix}'
            issue = IssueMock(
                label_names=[
                    label_name
                ]
            )
            expected_estimate = 2  # days
            actual_estimate = get_github_issue_estimate_label(issue, prefix, day_workhours=8)
            self.assertTrue(actual_estimate == expected_estimate, f'actual({actual_estimate}) != expected({expected_estimate}): {label_name}')

            label_name = f'{prefix}17{suffix}'
            issue = IssueMock(
                label_names=[
                    label_name
                ]
            )
            expected_estimate = 3  # days
            actual_estimate = get_github_issue_estimate_label(issue, prefix, day_workhours=8)
            self.assertTrue(actual_estimate == expected_estimate, f'actual({actual_estimate}) != expected({expected_estimate}): {label_name}')

    def test_get_github_issue_estimate_label_days(self):
        prefix = 'estimate:'
        for suffix in ('d', 'day', 'days'):
            label_name = f'{prefix}1{suffix}'
            issue = IssueMock(
                label_names=[
                    label_name
                ]
            )
            expected_estimate = 1  # days
            actual_estimate = get_github_issue_estimate_label(issue, prefix, day_workhours=8)
            self.assertTrue(actual_estimate == expected_estimate, f'actual({actual_estimate}) != expected({expected_estimate}): {label_name}')

            label_name = f'{prefix}2{suffix}'
            issue = IssueMock(
                label_names=[
                    label_name
                ]
            )
            expected_estimate = 2  # days
            actual_estimate = get_github_issue_estimate_label(issue, prefix, day_workhours=8)
            self.assertTrue(actual_estimate == expected_estimate, f'actual({actual_estimate}) != expected({expected_estimate}): {label_name}')

            label_name = f'{prefix}5{suffix}'
            issue = IssueMock(
                label_names=[
                    label_name
                ]
            )
            expected_estimate = 5  # days
            actual_estimate = get_github_issue_estimate_label(issue, prefix, day_workhours=8)
            self.assertTrue(actual_estimate == expected_estimate, f'actual({actual_estimate}) != expected({expected_estimate}): {label_name}')

    def test_get_github_issue_estimate_label_nosuffix(self):
        """Assumes 'days' if suffix not provided"""
        prefix = 'estimate:'
        suffix = ''
        label_name = f'{prefix}1{suffix}'
        issue = IssueMock(
            label_names=[
                label_name
            ]
        )
        expected_estimate = 1  # days
        actual_estimate = get_github_issue_estimate_label(issue, prefix, day_workhours=8)
        self.assertTrue(actual_estimate == expected_estimate, f'actual({actual_estimate}) != expected({expected_estimate}): {label_name}')

    def test_get_github_issue_estimate_label_multiestimatelabels(self):
        prefix = 'estimate:'
        suffix = 'd'
        label_name1 = f'{prefix}1{suffix}'
        label_name2 = f'{prefix}5{suffix}'
        issue = IssueMock(
            label_names=[
                label_name1,
                label_name2
            ]
        )
        expected_estimate = 5  # days
        actual_estimate = get_github_issue_estimate_label(issue, prefix, day_workhours=8)
        self.assertTrue(actual_estimate == expected_estimate, f'actual({actual_estimate}) != expected({expected_estimate}): {label_name1}, {label_name2}')

    def test_get_github_issue_category_label_singlelabel(self):
        prefix = 'category:'

        label_name = f'{prefix}help'
        issue = IssueMock(
            label_names=[
                label_name
            ]
        )
        expected_category = 'help'
        actual_category = get_github_issue_category_label(issue, prefix)
        self.assertTrue(actual_category == expected_category)

    def test_get_github_issue_category_label_multiplelabels(self):
        prefix = 'category:'

        label1_name = f'{prefix}help'
        label2_name = f'{prefix}other'
        issue = IssueMock(
            label_names=[
                label1_name,
                label2_name,
            ]
        )
        issue.html_url = 'https://www.someurl.com'
        with self.assertRaises(ValueError):
            actual_category = get_github_issue_category_label(issue, prefix)


class GetKippoProjectLoadTestCase(TestCase):
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
            user=self.user1,
            organization=self.organization,
            is_developer=True,
            email=f'otheruser@{self.domain}',
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        self.user1_membership.save()

        self.user2 = KippoUser(
            username='user2',
            github_login='user2',
            password='test',
            email='user2@github.com',
            is_staff=True,
        )
        self.user2.save()
        self.user2_membership = OrganizationMembership(
            user=self.user2,
            organization=self.organization,
            is_developer=True,
            wednesday=False,
            email=f'otheruser@{self.domain}',
            created_by=self.cli_manager,
            updated_by=self.cli_manager,
        )
        self.user2_membership.save()

        columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
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

    def test_get_projects_load(self):
        project_developer_load, latest_taskstatus_effort_date = get_projects_load(
            organization=self.organization,
            schedule_start_date=timezone.datetime(2019, 6, 5).date()
        )
        self.assertTrue(project_developer_load)
        self.assertTrue(latest_taskstatus_effort_date)

        expected_tasktitles = (
            'task1',
            'task2'
        )
        user1tasks = project_developer_load[self.kippoproject.id]['user1']
        self.assertTrue(any(t.title in expected_tasktitles for t in user1tasks))

        # qlu_task objects are attached to the returned user KippoTasks for later processing
        self.assertTrue(all(hasattr(t, 'qlu_task') for t in user1tasks))
        self.assertTrue(all(t.qlu_task.is_scheduled for t in user1tasks))

        # user1 works mon-fri, should start work on 6/5
        # -> work is defined in organizationmembership
        expected_effort_start_date = timezone.datetime(2019, 6, 5).date()
        actual_effort_start_date = min(t.qlu_task.start_date for t in user1tasks)
        self.assertTrue(actual_effort_start_date == expected_effort_start_date, f'actual({actual_effort_start_date}) != expected({expected_effort_start_date})')

        # 3 days estimate total starting from 6/5 (inclusive)
        expected_effort_end_date = timezone.datetime(2019, 6, 7).date()
        actual_effort_end_date = max(t.qlu_task.end_date for t in user1tasks)
        self.assertTrue(actual_effort_end_date == expected_effort_end_date, f'actual({actual_effort_end_date}) != expected({expected_effort_end_date})')

        expected_tasktitles = (
            'task3',
            'task4'
        )
        user2tasks = project_developer_load[self.kippoproject.id]['user2']
        self.assertTrue(any(t.title in expected_tasktitles for t in user2tasks))
        # qlu_task objects are attached to the returned user KippoTasks for later processing
        self.assertTrue(all(hasattr(t, 'qlu_task') for t in user2tasks))
        self.assertTrue(all(t.qlu_task.is_scheduled for t in user2tasks))

        # user2 does not 'work' on wednesday 6/5, so should start work on 6/6
        # -> work is defined in organizationmembership
        expected_effort_start_date = timezone.datetime(2019, 6, 6).date()
        actual_effort_start_date = min(t.qlu_task.start_date for t in user2tasks)
        self.assertTrue(actual_effort_start_date == expected_effort_start_date, f'actual({actual_effort_start_date}) != expected({expected_effort_start_date})')

        # 10 days estimate total starting 6/5 only counting workdays: mon, tues, thurs, fri
        expected_effort_end_date = timezone.datetime(2019, 6, 21).date()
        actual_effort_end_date = max(t.qlu_task.end_date for t in user2tasks)
        self.assertTrue(actual_effort_end_date == expected_effort_end_date, f'actual({actual_effort_end_date}) != expected({expected_effort_end_date})')

        expected_last_effort_date = timezone.datetime(2019, 6, 5).date()
        self.assertTrue(latest_taskstatus_effort_date == expected_last_effort_date, f'actual({latest_taskstatus_effort_date}) != expected({expected_last_effort_date})')

