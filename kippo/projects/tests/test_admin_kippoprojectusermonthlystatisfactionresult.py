from unittest.mock import MagicMock

from accounts.models import KippoUser, OrganizationMembership
from common.tests import DEFAULT_COLUMNSET_PK, DEFAULT_FIXTURES, IsStaffModelAdminTestCaseBase
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from projects.models import ProjectColumnSet

from ..admin import KippoProjectUserMonthlyStatisfactionResultAdmin
from ..models import KippoMilestone, KippoProject, KippoProjectUserMonthlyStatisfactionResult


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


class KippoProjectUserMonthlyStatisfactionResultAdminTestCase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
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
        self.anon_project = KippoProject.objects.create(
            organization=self.organization,
            name="anon_project-name",
            phase="anon-project",
            category="testing",
            columnset=columnset,
            start_date=self.current_date,
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
        OrganizationMembership.objects.create(organization=self.organization, user=self.organization_usera)
        self.organization_users = OrganizationMembership.objects.filter(organization=self.organization).values_list("user", flat=True)

        other_organization_usera = KippoUser.objects.create(username="other_organization_usera")
        OrganizationMembership.objects.create(organization=self.organization, user=other_organization_usera)

    def test_form__with_existing_entry(self):
        # create entry for this month
        KippoProjectUserMonthlyStatisfactionResult.objects.create(
            project=self.anon_project,
            date=timezone.now().date(),
            fullfillment_score=1,
            growth_score=1,
            created_by=self.organization_usera,
            updated_by=self.organization_usera,
        )
        assert KippoProjectUserMonthlyStatisfactionResult.objects.filter(created_by=self.organization_usera).count() == 1
        last_month = timezone.now().replace(day=1)
        last_month -= timezone.timedelta(days=1)
        last_month = last_month.replace(day=1).date()
        new_survey_date = f"{last_month.year}-{last_month.month}"
        data = {
            "project": self.anon_project.pk,
            "date_yearmonth": new_survey_date,
            "fullfillment_score": 1,
            "growth_score": 1,
        }
        modeladmin = KippoProjectUserMonthlyStatisfactionResultAdmin(KippoProjectUserMonthlyStatisfactionResult, self.site)
        url = reverse("admin:projects_kippoprojectusermonthlystatisfactionresult_add")
        request = self.factory.get(url)
        request.user = self.organization_usera
        ModelForm = modeladmin.get_form(request)
        form = ModelForm(data=data)
        self.assertTrue(form.is_valid(), f"new_survey_date={new_survey_date}, form.errors={form.errors}")
        obj = form.save(commit=False)
        modeladmin.save_model(request, obj, form, change=False)
        expected = 2
        self.assertEqual(KippoProjectUserMonthlyStatisfactionResult.objects.filter(created_by=self.organization_usera).count(), expected)
