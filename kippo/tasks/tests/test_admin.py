from commons.tests import DEFAULT_COLUMNSET_PK, IsStaffModelAdminTestCaseBase, setup_basic_project
from django.utils import timezone
from projects.models import KippoProject, ProjectColumnSet

from ..admin import KippoTaskAdmin, KippoTaskStatusAdmin
from ..models import KippoTask, KippoTaskStatus


class IsStaffOrganizationKippoTaskAdminTestCase(IsStaffModelAdminTestCaseBase):
    def test_list_objects(self):
        # create other org tasks
        create_objects = setup_basic_project(organization=self.organization)
        org_unassigned_user = self.organization.get_unassigned_kippouser()
        # create task with staff_user org
        self.kippoproject = create_objects["KippoProject"]
        active_states = self.kippoproject.columnset.get_active_column_names()
        active_state = active_states[0]

        task1 = KippoTask(
            title="task1",
            category="cat1",
            project=self.kippoproject,
            assignee=org_unassigned_user,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task1.save()
        task1status = KippoTaskStatus(
            task=task1,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=2,
            state=active_state,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task1status.save()

        other_org_unassigned_user = self.other_organization.get_unassigned_kippouser()
        default_columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        other_kippo_project = KippoProject(
            organization=self.other_organization,
            name="octocat-test-otherproject",
            github_project_html_url="https://github.com/orgs/githubcodesorg/projects/1",
            columnset=default_columnset,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        other_kippo_project.save()
        task2 = KippoTask(
            title="task2",
            category="cat2",
            project=other_kippo_project,
            assignee=other_org_unassigned_user,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task2.save()
        task2status = KippoTaskStatus(
            task=task2,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=1,
            state=active_state,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task2status.save()

        modeladmin = KippoTaskAdmin(KippoTask, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)

        # should list all tasks
        all_tasks_count = KippoTask.objects.count()
        self.assertTrue(all_tasks_count == len(qs))

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset_results = list(qs)
        expected_count = KippoTask.objects.filter(project__organization__in=self.staff_user_request.user.organizations).count()
        self.assertTrue(len(queryset_results) == expected_count, f"actual({len(queryset_results)}) != expected({expected_count})")

        staff_user_orgids = {o.id for o in self.staff_user_request.user.organizations}
        for task in queryset_results:
            task_org = {task.project.organization.id}
            self.assertTrue(staff_user_orgids.intersection(task_org))


class IsStaffOrganizationKippoTaskStatusAdminTestCase(IsStaffModelAdminTestCaseBase):
    def test_list_objects(self):
        # create other org tasks
        create_objects = setup_basic_project(organization=self.organization)
        org_unassigned_user = self.organization.get_unassigned_kippouser()
        # create task with staff_user org
        self.kippoproject = create_objects["KippoProject"]
        active_states = self.kippoproject.columnset.get_active_column_names()
        active_state = active_states[0]

        task1 = KippoTask(
            title="task1",
            category="cat1",
            project=self.kippoproject,
            assignee=org_unassigned_user,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task1.save()
        task1status = KippoTaskStatus(
            task=task1,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=2,
            state=active_state,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task1status.save()

        other_org_unassigned_user = self.other_organization.get_unassigned_kippouser()
        default_columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)
        other_kippo_project = KippoProject(
            organization=self.other_organization,
            name="octocat-test-otherproject",
            github_project_html_url="https://github.com/orgs/githubcodesorg/projects/1",
            columnset=default_columnset,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        other_kippo_project.save()
        task2 = KippoTask(
            title="task2",
            category="cat2",
            project=other_kippo_project,
            assignee=other_org_unassigned_user,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task2.save()
        task2status = KippoTaskStatus(
            task=task2,
            effort_date=timezone.datetime(2019, 6, 5).date(),
            estimate_days=1,
            state=active_state,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        task2status.save()

        modeladmin = KippoTaskStatusAdmin(KippoTaskStatus, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)
        actual = len(qs)
        # should list all tasks
        expected = KippoTaskStatus.objects.count()
        self.assertTrue(actual == expected, f"actual({actual}) != expected({expected})")

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset_results = list(qs)
        expected_count = KippoTaskStatus.objects.filter(task__project__organization__in=self.staff_user_request.user.organizations).count()
        self.assertTrue(len(queryset_results) == expected_count, f"actual({len(queryset_results)}) != expected({expected_count})")

        staff_user_orgids = {o.id for o in self.staff_user_request.user.organizations}
        for taskstatus in queryset_results:
            taskstatus_org = {taskstatus.task.project.organization.id}
            self.assertTrue(staff_user_orgids.intersection(taskstatus_org))
