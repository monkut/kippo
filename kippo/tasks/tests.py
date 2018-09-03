from django.test import TestCase
from django.utils import timezone
from accounts.models import KippoUser
from projects.models import KippoProject, KippoMilestone


class KippoTaskAssignmentTestCase(TestCase):

    def test_multiple_assignees(self):
        raise NotImplementedError()

    def test_multiple_assignees_one_removed(self):
        raise NotImplementedError()


class KippoTaskUpdateTestCase(TestCase):

    def setUp(self):
        start_date = timezone.datetime(2018, 7, 16).date()
        target_date = timezone.datetime(2018, 11, 1).date()
        self.test_project_1 = KippoProject(name='Test Project-1',
                                    start_date=start_date,
                                    target_date=target_date)
        self.test_project_1.save()

        milestone_initial_target_date = timezone.datetime(2018, 8, 1).date()
        self.test_project_1_milestone_initial = KippoMilestone(project=self.test_project_1,
                                                               start_date=start_date,
                                                               target_date=milestone_initial_target_date)
        self.test_project_1_milestone_initial.save()

        self.assignee_1 = KippoUser

    def test_state_done(self):
        raise NotImplementedError()

    def test_normal_scheduled_update(self):
        raise NotImplementedError()


class ChartTestCase(TestCase):

    def test_functions_get_projects_load(self):
        raise NotImplementedError()

