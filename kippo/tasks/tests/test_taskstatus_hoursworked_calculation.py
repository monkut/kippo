import datetime

from common.tests import DEFAULT_FIXTURES, setup_basic_project
from django.test import TestCase
from django.utils import timezone

from ..functions import update_kippotaskstatus_hours_worked
from ..models import KippoTaskStatus


class CalculateKippoTaskStatusHoursWorkedTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created_objects = setup_basic_project()
        user = created_objects["KippoUser"]

        # get active column state names
        self.project = created_objects["KippoProject"]
        active_state_names = self.project.get_active_column_names()

        # create task status
        kippo_task = created_objects["KippoTask"]

        tz = timezone.get_current_timezone()
        first_effort_date = timezone.make_aware(datetime.datetime(2018, 9, 3), tz).date()  # monday
        self.kippotaskstatus_first = KippoTaskStatus(
            task=kippo_task,
            state=active_state_names[0],
            effort_date=first_effort_date.strftime("%Y-%m-%d"),
            estimate_days=5,
            created_by=user,
            updated_by=user,
        )
        self.kippotaskstatus_first.save()

        self.second_effort_date = first_effort_date + datetime.timedelta(days=1)  # tuesday
        self.kippotaskstatus_second = KippoTaskStatus(
            task=kippo_task,
            state=active_state_names[0],
            effort_date=self.second_effort_date.strftime("%Y-%m-%d"),
            estimate_days=4,
            created_by=user,
            updated_by=user,
        )
        self.kippotaskstatus_second.save()

    def test_estimate_decrease(self):
        projects = [self.project]
        self.kippotaskstatus_first.estimate_days = 5
        self.kippotaskstatus_first.save()
        self.kippotaskstatus_second.estimate_days = 4
        self.kippotaskstatus_second.hours_spent = None
        self.kippotaskstatus_second.save()

        assert self.kippotaskstatus_first.hours_spent is None
        assert self.kippotaskstatus_second.hours_spent is None
        target_taskstatus_id = self.kippotaskstatus_second.id
        results = update_kippotaskstatus_hours_worked(projects, self.second_effort_date)
        self.assertTrue(len(results) == 1, results)
        updated_status = results[0]

        self.assertTrue(target_taskstatus_id == updated_status.id)

        # For a single day difference the results should be:
        # organization.day_workhours

        expected_hours_spent = 8
        self.assertTrue(updated_status.hours_spent == expected_hours_spent)

        # make sure the original was not updated
        self.kippotaskstatus_first.refresh_from_db()
        self.assertTrue(self.kippotaskstatus_first.hours_spent is None)

    def test_estimate_decrease_float(self):
        projects = [self.project]
        self.kippotaskstatus_first.estimate_days = 5
        self.kippotaskstatus_first.save()
        self.kippotaskstatus_second.estimate_days = 4.5
        self.kippotaskstatus_second.hours_spent = None
        self.kippotaskstatus_second.save()

        assert self.kippotaskstatus_first.hours_spent is None
        assert self.kippotaskstatus_second.hours_spent is None
        target_taskstatus_id = self.kippotaskstatus_second.id
        results = update_kippotaskstatus_hours_worked(projects, self.second_effort_date)
        self.assertTrue(len(results) == 1, results)
        updated_status = results[0]

        self.assertTrue(target_taskstatus_id == updated_status.id)

        # For a single day difference the results should be:
        # organization.day_workhours

        expected_hours_spent = 4
        self.assertTrue(updated_status.hours_spent == expected_hours_spent)

        # make sure the original was not updated
        self.kippotaskstatus_first.refresh_from_db()
        self.assertTrue(self.kippotaskstatus_first.hours_spent is None)

    def test_estimate_increase(self):
        projects = [self.project]
        self.kippotaskstatus_first.estimate_days = 4
        self.kippotaskstatus_first.save()
        self.kippotaskstatus_second.estimate_days = 15
        self.kippotaskstatus_second.save()

        assert self.kippotaskstatus_first.hours_spent is None
        assert self.kippotaskstatus_second.hours_spent is None
        results = update_kippotaskstatus_hours_worked(projects, self.second_effort_date)
        self.assertTrue(len(results) == 0, results)

        self.kippotaskstatus_first.refresh_from_db()
        self.kippotaskstatus_second.refresh_from_db()
        self.assertTrue(self.kippotaskstatus_first.hours_spent is None)
        self.assertTrue(self.kippotaskstatus_second.hours_spent is None)

    def test_estimate_increase_float(self):
        projects = [self.project]
        self.kippotaskstatus_first.estimate_days = 4
        self.kippotaskstatus_first.save()
        self.kippotaskstatus_second.estimate_days = 15.5
        self.kippotaskstatus_second.save()

        assert self.kippotaskstatus_first.hours_spent is None
        assert self.kippotaskstatus_second.hours_spent is None
        results = update_kippotaskstatus_hours_worked(projects, self.second_effort_date)
        self.assertTrue(len(results) == 0, results)

        self.kippotaskstatus_first.refresh_from_db()
        self.kippotaskstatus_second.refresh_from_db()
        self.assertTrue(self.kippotaskstatus_first.hours_spent is None)
        self.assertTrue(self.kippotaskstatus_second.hours_spent is None)

    def test_estimate_float(self):
        self.kippotaskstatus_first.estimate_days = 4.5
        self.kippotaskstatus_first.save()
        self.assertTrue(isinstance(self.kippotaskstatus_first.estimate_days, float))
