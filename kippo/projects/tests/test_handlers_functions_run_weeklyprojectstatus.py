from unittest import mock

from commons.tests import IsStaffModelAdminTestCaseBase, setup_basic_project
from django.utils import timezone

from projects.functions import previous_week_startdate
from projects.managers import ProjectSlackManager
from projects.models import ActiveKippoProject, KippoProjectStatus


class RunWeeklyProjectStatusTestCase(IsStaffModelAdminTestCaseBase):
    """Test case for the run_weekly_project_status function."""

    def setUp(self):
        super().setUp()
        self.organization.enable_slack_channel_reporting = True
        self.organization.save()
        KippoProjectStatus.objects.all().delete()

    def _prepare(self, create_project_status: bool = False, status_datetime: timezone.datetime | None = None) -> list[KippoProjectStatus]:
        created_objects = setup_basic_project(organization=self.organization)

        project_end_date = timezone.now() - timezone.timedelta(days=7)
        self.project1 = created_objects["KippoProject"]
        # update project end date to 1 week ago
        contract_complete_confidence = 100
        self.project1.confidence = contract_complete_confidence
        self.project1.target_date = project_end_date
        self.project1.is_closed = False
        self.project1.save()

        self.project2 = created_objects["KippoProject2"]
        # update project end date to 1 week ago
        self.project2.confidence = contract_complete_confidence
        self.project2.target_date = project_end_date
        self.project2.is_closed = False
        self.project2.save()

        # self.organization user
        created_status_entries = []
        if create_project_status:
            project_status = KippoProjectStatus.objects.create(
                project=self.project1,
                comment="test comment",
                created_by=self.staffuser_with_org,
                updated_by=self.staffuser_with_org,
            )
            if status_datetime:
                assert status_datetime.tzinfo is not None, "status_datetime must be timezone-aware"
                project_status.created_datetime = status_datetime
                project_status.updated_datetime = status_datetime
                project_status.save()
            created_status_entries.append(project_status)
        return created_status_entries

    @mock.patch("projects.managers.WebClient.chat_postMessage", return_value={"ok": True})
    def test_2_projects__with_status_comments(self, *_):
        week_start_date = previous_week_startdate()
        comment_status_datetime = timezone.datetime.combine(
            week_start_date + timezone.timedelta(days=1), timezone.datetime.min.time(), tzinfo=timezone.get_default_timezone()
        )
        entries = self._prepare(create_project_status=True, status_datetime=comment_status_datetime)
        expected_entry_count = 1
        assert len(entries) == expected_entry_count, f"Expected no project status entries to be created, got: {len(entries)}"
        for entry in entries:
            assert entry.created_datetime == comment_status_datetime, (
                f"Expected created_datetime to be {comment_status_datetime}, got: {entry.created_datetime}"
            )
            assert entry.project.is_closed is False

        expected_project_count = 2
        active_project_count = ActiveKippoProject.objects.filter(organization=self.organization).count()
        assert ActiveKippoProject.objects.filter(organization=self.organization).count() == expected_project_count, (
            f"Expected {expected_project_count} active projects, got: {active_project_count}"
        )
        manager = ProjectSlackManager(self.organization)
        week_start_date = previous_week_startdate()
        blocks, response = manager.post_weekly_project_status(week_start_date=week_start_date)
        self.assertTrue(blocks)
