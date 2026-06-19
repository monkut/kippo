import datetime

from accounts.models import Country, KippoUser, OrganizationMembership, PersonalHoliday, PublicHoliday
from commons.definitions import SATURDAY, SUNDAY
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from tasks.models import KippoTask, KippoTaskStatus

from projects.definitions import (
    DEFAULT_PROJECT_CATEGORY_VALUE,
    FULL_CONFIDENCE_PERCENTAGE,
    KIPPOPROJECT_CATEGORY_CHOICES,
    NON_PROJECT_CATEGORY_VALUE,
    UPSELL_CATEGORY_VALUES,
    VALID_KIPPOPROJECT_CATEGORY_VALUES,
)
from projects.models import (
    DEFAULT_PROJECT_PHASE,
    PHASE_CONFIDENCE,
    VALID_PROJECT_PHASES,
    KippoMilestone,
    KippoProject,
    KippoProjectOrganizationCategory,
)


class KippoProjectMethodsTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.repository = created["GithubRepository"]
        self.task1 = created["KippoTask"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        # default columnset done name
        self.planning_column_name = "planning"
        self.done_column_name = "done"

        # create task2
        self.task2 = KippoTask(
            title="task2",
            category="test category",
            project=self.project,
            assignee=self.user,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            github_issue_html_url=f"https://github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/2",
            github_issue_api_url=f"https://api.github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/2",
        )
        self.task2.save()

        # create task3
        self.task3 = KippoTask(
            title="task3",
            category="test category",
            project=self.project,
            assignee=self.user,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            github_issue_html_url=f"https://github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/3",
            github_issue_api_url=f"https://api.github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/3",
        )
        self.task3.save()

        self.firstdate = timezone.datetime(2019, 8, 14).date()
        # create KippoTaskStatus objects
        # create existing taskstatus
        self.task1_status1 = KippoTaskStatus(
            task=self.task1,
            state=self.planning_column_name,
            effort_date=self.firstdate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task1_status1.save()

        self.task1_seconddate = timezone.datetime(2019, 8, 17).date()
        self.task1_status2 = KippoTaskStatus(
            task=self.task1,
            state=self.planning_column_name,
            effort_date=self.task1_seconddate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task1_status2.save()

        self.task2_status1 = KippoTaskStatus(
            task=self.task2,
            state=self.planning_column_name,
            effort_date=self.firstdate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task2_status1.save()

        self.task2_seconddate = timezone.datetime(2019, 8, 19).date()
        self.task2_status2 = KippoTaskStatus(
            task=self.task2,
            state=self.planning_column_name,
            effort_date=self.task2_seconddate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task2_status2.save()

        # task3 taskstatus
        self.task3_status1 = KippoTaskStatus(
            task=self.task3,
            state=self.done_column_name,
            effort_date=self.firstdate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task3_status1.save()

    def test_get_active_taskstatus__no_max_date(self):
        results, has_estimates = self.project.get_active_taskstatus()

        expected = 2
        actual = len(results)
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected}): {results}")

        actual_tasks = [s.task for s in results]
        self.assertTrue(self.task3 not in actual_tasks, f"done task({self.task3}) should not be returned but is: {results}")

        expected_tasks = [self.task1, self.task2]
        self.assertTrue(all(t in expected_tasks for t in actual_tasks))
        self.assertTrue(all(t in actual_tasks for t in expected_tasks))

        task1_tested = False
        task2_tested = False
        for taskstatus in results:
            if taskstatus.task == self.task1:
                self.assertTrue(taskstatus.effort_date == self.task1_seconddate)
                task1_tested = True
            elif taskstatus.task == self.task2:
                self.assertTrue(taskstatus.effort_date == self.task2_seconddate)
                task2_tested = True
        self.assertTrue(all([task1_tested, task2_tested]))

    def test__get_active_taskstatus_from_projects__with_max_effort_date(self):
        max_effort_date = timezone.datetime(2019, 8, 15).date()
        results, has_estimates = self.project.get_active_taskstatus(max_effort_date=max_effort_date)

        expected_activetask_count = 2
        self.assertEqual(len(results), expected_activetask_count)
        actual_tasks = [s.task for s in results]
        self.assertTrue(self.task3 not in actual_tasks, f"done task({self.task3}) should not be returned but is: {results}")

        expected_tasks = [self.task1, self.task2]
        self.assertTrue(all(t in expected_tasks for t in actual_tasks))
        self.assertTrue(all(t in actual_tasks for t in expected_tasks))

        task1_tested = False
        task2_tested = False
        for taskstatus in results:
            if taskstatus.task == self.task1:
                self.assertTrue(taskstatus.effort_date == self.firstdate)
                task1_tested = True
            elif taskstatus.task == self.task2:
                self.assertTrue(taskstatus.effort_date == self.firstdate)
                task2_tested = True
        self.assertTrue(all([task1_tested, task2_tested]))

    def test__get_active_taskstatus__done__latest_taskstatus(self):
        new_date = timezone.datetime(2019, 12, 19)
        for _ in range(10):
            task2_status = KippoTaskStatus(
                task=self.task2,
                state=self.done_column_name,
                effort_date=new_date.date(),
                estimate_days=3,
                created_by=self.github_manager,
                updated_by=self.github_manager,
            )
            task2_status.save()
            new_date += datetime.timedelta(days=1)

        # make sure that task2 is not returned now that it is 'done'
        results, has_estimates = self.project.get_active_taskstatus()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], self.task1_status2)

    def test_related_github_repositories(self):
        assert self.repository
        assert self.task1.project == self.project
        assert self.task1.github_issue_html_url
        assert self.repository.html_url
        expected = [self.repository]
        actual = list(self.project.related_github_repositories())
        self.assertEqual(actual, expected)

    def test_save_strips_leading_hash_from_slack_channel_name(self):
        self.project.slack_channel_name = "#proj-foo"
        self.project.save()
        self.project.refresh_from_db()
        self.assertEqual(self.project.slack_channel_name, "proj-foo")

    def test_save_strips_surrounding_whitespace_from_slack_channel_name(self):
        self.project.slack_channel_name = "  proj-foo  "
        self.project.save()
        self.project.refresh_from_db()
        self.assertEqual(self.project.slack_channel_name, "proj-foo")

    def test_save_strips_whitespace_then_leading_hash(self):
        self.project.slack_channel_name = "  #proj-foo "
        self.project.save()
        self.project.refresh_from_db()
        self.assertEqual(self.project.slack_channel_name, "proj-foo")

    def test_save_preserves_already_normalized_slack_channel_name(self):
        self.project.slack_channel_name = "proj-foo"
        self.project.save()
        self.project.refresh_from_db()
        self.assertEqual(self.project.slack_channel_name, "proj-foo")

    def test_save_leaves_empty_slack_channel_name_empty(self):
        self.project.slack_channel_name = ""
        self.project.save()
        self.project.refresh_from_db()
        self.assertEqual(self.project.slack_channel_name, "")


class KippoMilestoneMethodsTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.repository = created["GithubRepository"]
        self.task1 = created["KippoTask"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

        self.user2 = KippoUser(username="user2", github_login="user2", password="test", email="a@github.com", is_staff=True)  # noqa: S106
        self.user2.save()

        orgmembership = OrganizationMembership(
            user=self.user2,
            organization=self.organization,
            is_developer=True,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        orgmembership.save()

        # default columnset done name
        self.planning_column_name = "planning"
        self.done_column_name = "done"

        # prepare tasks
        self.task2 = KippoTask(
            title="task2",
            category="test category",
            project=self.project,
            assignee=self.user2,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            github_issue_html_url=f"https://github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/2",
            github_issue_api_url=f"https://api.github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/2",
        )
        self.task2.save()

        # create task3
        self.task3 = KippoTask(
            title="task3",
            category="test category",
            project=self.project,
            assignee=self.user2,
            created_by=self.github_manager,
            updated_by=self.github_manager,
            github_issue_html_url=f"https://github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/3",
            github_issue_api_url=f"https://api.github.com/repos/{self.organization.github_organization_name}/{self.repository.name}/issues/3",
        )
        self.task3.save()

        self.firstdate = timezone.datetime(2019, 8, 14).date()
        # create KippoTaskStatus objects
        # create existing taskstatus
        self.task1_status1 = KippoTaskStatus(
            task=self.task1,
            state=self.planning_column_name,
            effort_date=self.firstdate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task1_status1.save()

        self.task1_seconddate = timezone.datetime(2019, 8, 17).date()
        self.task1_status2 = KippoTaskStatus(
            task=self.task1,
            state=self.planning_column_name,
            effort_date=self.task1_seconddate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task1_status2.save()

        self.task2_status1 = KippoTaskStatus(
            task=self.task2,
            state=self.planning_column_name,
            effort_date=self.firstdate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task2_status1.save()

        self.task2_seconddate = timezone.datetime(2019, 8, 19).date()
        self.task2_status2 = KippoTaskStatus(
            task=self.task2,
            state=self.planning_column_name,
            effort_date=self.task2_seconddate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task2_status2.save()

        # task3 taskstatus
        self.task3_status1 = KippoTaskStatus(
            task=self.task3,
            state=self.done_column_name,
            effort_date=self.firstdate,
            estimate_days=3,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.task3_status1.save()

        self.country = Country(name="Japan", alpha_2="jp", alpha_3="jpn", country_code="123", region="Asia")
        self.country.save()
        self.user.holiday_country = self.country
        self.user.save()

    def test_estimated_completion_date(self):
        assert KippoProject.objects.count() > 0
        # set start_date, target_date for project
        self.project.start_date = timezone.datetime(2020, 9, 1).date()
        self.project.target_date = timezone.datetime(2020, 11, 1).date()
        self.project.save()

        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()

        # assign milestone to tasks
        self.task1.milestone = kippomilestone_1
        self.task1.save()

        self.assertGreater(kippomilestone_1.estimated_completion_date, milestone1_startdate)

    def test_available_work_days(self):
        # defined in setUp() setup_basic_project()
        # includes:
        # - organization unassigned user
        # - created user
        # - created user2
        expected_mempership_count = 3
        assert OrganizationMembership.objects.count() == expected_mempership_count
        # remove user2 OrganizationMembership -- affects the available days calculation
        OrganizationMembership.objects.filter(user=self.user2).delete()

        # created OrganizationMembership defaults to 5 (mon-fri) days
        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()

        # assign milestone to tasks
        self.task1.milestone = kippomilestone_1
        self.task1.save()

        actual = kippomilestone_1.available_work_days(start_date=timezone.datetime(2020, 9, 1).date())
        self.assertTrue(actual)

        # all days mon-fri between 9/1 to 9/20
        expected = sum(1 for day in range(1, 21, 1) if timezone.datetime(2020, 9, day).weekday() not in (SATURDAY, SUNDAY))
        self.assertEqual(actual, expected)

        # add personal holday and check again
        personalholiday = PersonalHoliday(
            user=self.user,
            day=timezone.datetime(2020, 9, 7).date(),  # monday
        )
        personalholiday.save()
        actual = kippomilestone_1.available_work_days(start_date=timezone.datetime(2020, 9, 1).date())
        self.assertTrue(actual)
        expected -= 1
        self.assertEqual(actual, expected)

        # add public holiday and check again
        public_holiday = PublicHoliday(
            country=self.country,
            name="test-public-holiday",
            day=timezone.datetime(2020, 9, 8).date(),  # tuesday
        )
        public_holiday.save()

        actual = kippomilestone_1.available_work_days(start_date=timezone.datetime(2020, 9, 1).date())
        self.assertTrue(actual)
        expected -= 1
        self.assertEqual(actual, expected)

    def test_estimated_work_days(self):
        expected_project_count = 0
        assert KippoProject.objects.count() > expected_project_count

        expected_taskstatus_count = 5
        assert KippoTaskStatus.objects.count() == expected_taskstatus_count, KippoTaskStatus.objects.count()
        # set start_date, target_date for project
        self.project.start_date = timezone.datetime(2020, 9, 1).date()
        self.project.target_date = timezone.datetime(2020, 11, 1).date()
        self.project.save()

        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()
        kippomilestone_1.skip_cache = True

        # assign milestone to tasks
        self.task1.milestone = kippomilestone_1
        self.task1.save()

        actual = kippomilestone_1.estimated_work_days
        self.assertTrue(actual)

        task1_taskstatus = self.task1.latest_kippotaskstatus()
        expected = task1_taskstatus.estimate_days
        self.assertEqual(actual, expected)

        # add another task from another user
        self.task2.milestone = kippomilestone_1
        self.task2.save()
        task2_taskstatus = self.task2.latest_kippotaskstatus()

        self.task3.milestone = kippomilestone_1  # this task is done and SHOULD NOT be counted
        self.task3.save()

        expected = task2_taskstatus.estimate_days + task1_taskstatus.estimate_days
        actual = kippomilestone_1.estimated_work_days
        self.assertEqual(actual, expected)

    def test_tasks(self):
        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()

        # assign milestone to tasks
        self.task1.milestone = kippomilestone_1
        self.task1.save()

        milestone_tasks = list(kippomilestone_1.tasks)

        expected = 1
        self.assertEqual(len(milestone_tasks), expected)

        milestone_task = milestone_tasks[0]
        self.assertEqual(milestone_task.id, self.task1.id)

    def test_active_tasks(self):
        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()

        # assign milestone to tasks
        self.task1.milestone = kippomilestone_1
        self.task1.save()

        # assign task3 (done) to milestone
        self.task3.milestone = kippomilestone_1
        self.task3.save()

        all_tasks = list(kippomilestone_1.tasks)
        expected = 2
        self.assertEqual(len(all_tasks), expected)

        active_tasks = list(kippomilestone_1.active_tasks)

        expected = 1
        self.assertEqual(len(active_tasks), expected)

        milestone_task = active_tasks[0]
        self.assertEqual(milestone_task.id, self.task1.id)

        active_task_states = self.project.columnset.get_active_column_names()
        for task in active_tasks:
            status = task.latest_kippotaskstatus()
            self.assertIn(status.state, active_task_states)

    def test_delete_milestone(self):
        """Confirm that deleting the milestone does NOT delete attached tasks"""
        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()

        # assign milestone to tasks
        self.task1.milestone = kippomilestone_1
        self.task1.save()
        task1_id = self.task1.id

        # delete milestone
        kippomilestone_1.delete()

        # confirm task still exists
        self.assertTrue(KippoTask.objects.filter(id=task1_id).exists())

    def test_get_assignee_task_counts(self):
        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()

        # assign milestone to tasks
        self.task1.milestone = kippomilestone_1
        self.task1.save()

        self.task2.milestone = kippomilestone_1
        self.task2.save()

        # assign task3 (done) to milestone
        self.task3.milestone = kippomilestone_1
        self.task3.save()

        user1_active_tasks = 1
        user2_active_tasks = 1
        expected = user1_active_tasks + user2_active_tasks
        # returns "active" task counts
        actual = kippomilestone_1.get_assignee_task_counts()
        self.assertEqual(sum(actual.values()), expected)

        expected = 2
        self.assertEqual(len(actual.keys()), expected)

    def test_get_assignee_estimated_workdays(self):
        milestone1_startdate = timezone.datetime(2020, 9, 1).date()
        milestone1_targetdate = timezone.datetime(2020, 9, 20).date()
        kippomilestone_1 = KippoMilestone(
            project=self.project,
            title="test milestone 1",
            start_date=milestone1_startdate,
            target_date=milestone1_targetdate,
        )
        kippomilestone_1.save()

        # assign milestone to tasks
        self.task1.milestone = kippomilestone_1
        self.task1.save()

        self.task2.milestone = kippomilestone_1
        self.task2.save()

        # assign task3 (done) to milestone
        self.task3.milestone = kippomilestone_1
        self.task3.save()

        expected_user1_estimated_workdays = 3
        expected_user2_estimated_workdays = 3
        expected = expected_user1_estimated_workdays + expected_user2_estimated_workdays
        actual = kippomilestone_1.get_assignee_estimated_workdays()
        self.assertEqual(sum(actual.values()), expected, actual)

        expected_assignee_count = 2
        self.assertEqual(len(actual), expected_assignee_count)
        self.assertIn(self.task1.assignee, actual)
        self.assertIn(self.task2.assignee, actual)


class KippoProjectCategoryChoicesTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def test_global_default_categories_seeded(self):
        for key, _label in KIPPOPROJECT_CATEGORY_CHOICES:
            self.assertTrue(
                KippoProjectOrganizationCategory.objects.filter(organization__isnull=True, key=key).exists(),
                msg=f"global default category not seeded: {key}",
            )

    def test_category_field_is_fk_defaulting_to_other(self):
        field = KippoProject._meta.get_field("category")
        self.assertTrue(field.is_relation)
        self.assertEqual(field.related_model, KippoProjectOrganizationCategory)
        other = KippoProjectOrganizationCategory.objects.get(organization__isnull=True, key=DEFAULT_PROJECT_CATEGORY_VALUE)
        self.assertEqual(field.default(), other.pk)
        self.assertEqual(settings.DEFAULT_KIPPOPROJECT_CATEGORY, DEFAULT_PROJECT_CATEGORY_VALUE)

    def test_upsell_category_values_present_as_global_categories(self):
        for value in UPSELL_CATEGORY_VALUES:
            self.assertIn(value, VALID_KIPPOPROJECT_CATEGORY_VALUES)
            self.assertTrue(KippoProjectOrganizationCategory.objects.filter(organization__isnull=True, key=value).exists())

    def test_close_comment_field_defaults(self):
        field = KippoProject._meta.get_field("close_comment")
        self.assertTrue(field.blank)
        self.assertEqual(field.default, "")

    def test_parent_project_field_is_self_fk_with_set_null(self):
        field = KippoProject._meta.get_field("parent_project")
        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        self.assertEqual(field.related_model, KippoProject)


class KippoProjectOrganizationCategoryTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]

    def test_get_for_organization_includes_globals_and_org_specific(self):
        KippoProjectOrganizationCategory.objects.create(
            organization=self.organization, key="org-special", label="Org Special", sort_order=5, created_by=self.user, updated_by=self.user
        )
        keys = set(KippoProjectOrganizationCategory.get_for_organization(self.organization).values_list("key", flat=True))
        self.assertIn("org-special", keys)
        self.assertIn(DEFAULT_PROJECT_CATEGORY_VALUE, keys)
        self.assertIn(NON_PROJECT_CATEGORY_VALUE, keys)

    def test_get_for_organization_excludes_inactive(self):
        KippoProjectOrganizationCategory.objects.create(
            organization=self.organization, key="hidden", label="Hidden", is_active=False, created_by=self.user, updated_by=self.user
        )
        keys = set(KippoProjectOrganizationCategory.get_for_organization(self.organization).values_list("key", flat=True))
        self.assertNotIn("hidden", keys)

    def test_new_project_defaults_to_other_category(self):
        # setup_basic_project creates projects without an explicit category — they take the model default.
        self.assertEqual(self.project.category.key, DEFAULT_PROJECT_CATEGORY_VALUE)


class KippoProjectPhaseStatusTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        self.project = setup_basic_project()["KippoProject"]

    def test_phase_choices_are_the_status_values(self):
        choices = dict(KippoProject._meta.get_field("phase").choices)
        self.assertEqual(set(choices), set(dict(VALID_PROJECT_PHASES)))
        self.assertIn("under-contract", choices)
        self.assertIn("non-project", [c.key for c in KippoProjectOrganizationCategory.objects.all()])  # anon relocated to category
        self.assertNotIn("anon-project", choices)

    def test_default_phase_and_derived_confidence(self):
        self.assertEqual(self.project.phase, DEFAULT_PROJECT_PHASE)
        self.assertEqual(self.project.confidence, PHASE_CONFIDENCE[DEFAULT_PROJECT_PHASE])
        self.assertFalse(KippoProject._meta.get_field("confidence").editable)

    def test_confidence_is_rederived_when_phase_changes_on_save(self):
        for phase, expected in PHASE_CONFIDENCE.items():
            self.project.phase = phase  # phase changes each iteration → confidence re-derived
            self.project.confidence = 0  # a user-set value is overwritten only because phase changed
            self.project.save()
            self.project.refresh_from_db()
            self.assertEqual(self.project.confidence, expected, msg=f"{phase} -> {expected}")

    def test_confidence_preserved_when_phase_unchanged(self):
        # An existing / manually-set confidence survives an edit that does not touch phase.
        KippoProject.objects.filter(pk=self.project.pk).update(confidence=55)
        reloaded = KippoProject.objects.get(pk=self.project.pk)  # from_db snapshots the persisted phase
        self.assertEqual(reloaded.confidence, 55)
        reloaded.problem_definition = "edited, phase untouched"
        reloaded.save()
        reloaded.refresh_from_db()
        self.assertEqual(reloaded.confidence, 55)
        self.assertEqual(reloaded.phase, DEFAULT_PROJECT_PHASE)

    def test_confidence_rederived_only_when_phase_actually_changes(self):
        # Same instance, manual confidence, then a phase change → confidence re-derived from phase.
        KippoProject.objects.filter(pk=self.project.pk).update(confidence=55)
        reloaded = KippoProject.objects.get(pk=self.project.pk)
        reloaded.phase = "under-contract"
        reloaded.save()
        reloaded.refresh_from_db()
        self.assertEqual(reloaded.confidence, PHASE_CONFIDENCE["under-contract"])

    def test_only_contract_and_completed_reach_full_confidence(self):
        full = {phase for phase, conf in PHASE_CONFIDENCE.items() if conf == FULL_CONFIDENCE_PERCENTAGE}
        self.assertEqual(full, {"under-contract", "completed"})
