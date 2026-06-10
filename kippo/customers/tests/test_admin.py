from datetime import date
from http import HTTPStatus
from unittest.mock import MagicMock

from accounts.models import KippoUser, OrganizationMembership
from commons.tests import DEFAULT_FIXTURES, IsStaffModelAdminTestCaseBase, default_project_category
from django import forms
from django.urls import reverse
from projects.models import KippoProject
from projects.tests.test_admin import KippoProjectAdminFixtureTestCaseBase

from customers.admin import KippoCustomerAdmin, KippoProjectReadOnlyInline
from customers.models import KippoCustomer


class IsStaffOrganizationKippoCustomerAdminTestCase(IsStaffModelAdminTestCaseBase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        # Customers in the user's org and another org
        self.customer_in_org = KippoCustomer.objects.create(
            organization=self.organization,
            name="Acme",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.customer_in_other_org = KippoCustomer.objects.create(
            organization=self.other_organization,
            name="Globex",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def test_superuser_sees_all_customers(self):
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.customer_in_org.id, ids)
        self.assertIn(self.customer_in_other_org.id, ids)

    def test_staffuser_only_sees_own_org_customers(self):
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        qs = modeladmin.get_queryset(self.staff_user_request)
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.customer_in_org.id, ids)
        self.assertNotIn(self.customer_in_other_org.id, ids)


class KippoCustomerAdminProjectsInlineTestCase(KippoProjectAdminFixtureTestCaseBase):
    """KippoCustomerAdmin shows a read-only inline listing the customer's related projects,
    and an active-project-count column on the changelist.
    """

    def setUp(self):
        super().setUp()
        self.customer = KippoCustomer.objects.create(
            organization=self.organization,
            name="Acme",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.other_customer = KippoCustomer.objects.create(
            organization=self.organization,
            name="Globex",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def _make_customer_project(self, name: str, customer: KippoCustomer, target_date: date, billing_date: date) -> KippoProject:
        return KippoProject.objects.create(
            organization=self.organization,
            name=name,
            category=default_project_category(),
            columnset=self.columnset,
            customer=customer,
            target_date=target_date,
            billing_date=billing_date,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def test_inline_registered_and_read_only(self):
        self.assertIn(KippoProjectReadOnlyInline, KippoCustomerAdmin.inlines)
        inline = KippoProjectReadOnlyInline(KippoCustomer, self.site)
        self.assertFalse(inline.has_add_permission(self.staff_user_request))
        self.assertFalse(inline.can_delete)
        # every displayed field is read-only
        self.assertEqual(set(inline.fields), set(inline.readonly_fields))
        for fieldname in ("get_project_link", "start_date", "target_date", "billing_date"):
            self.assertIn(fieldname, inline.fields)

    def test_change_view_lists_related_project_with_admin_link_and_billing_date(self):
        project = self._make_customer_project("acme-alpha", self.customer, date(2026, 6, 1), date(2026, 6, 15))
        url = reverse("admin:customers_kippocustomer_change", args=[self.customer.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        content = response.content.decode()
        self.assertIn(project.name, content)
        self.assertIn(project.get_admin_url(), content)
        self.assertIn("2026-06-15", content)  # billing_date rendered

    def test_change_view_orders_projects_by_target_date_ascending(self):
        later = self._make_customer_project("acme-later", self.customer, date(2026, 9, 1), date(2026, 9, 1))
        earlier = self._make_customer_project("acme-earlier", self.customer, date(2026, 3, 1), date(2026, 3, 1))
        url = reverse("admin:customers_kippocustomer_change", args=[self.customer.id])
        content = self.client.get(url).content.decode()
        self.assertLess(content.index(earlier.name), content.index(later.name))

    def test_change_view_excludes_other_customers_projects(self):
        mine = self._make_customer_project("acme-mine", self.customer, date(2026, 6, 1), date(2026, 6, 1))
        theirs = self._make_customer_project("globex-theirs", self.other_customer, date(2026, 6, 1), date(2026, 6, 1))
        url = reverse("admin:customers_kippocustomer_change", args=[self.customer.id])
        content = self.client.get(url).content.decode()
        self.assertIn(mine.name, content)
        self.assertNotIn(theirs.name, content)

    def test_changelist_active_project_count_excludes_closed_and_inactive(self):
        # Two active projects (is_closed=False, display_as_active=True by default).
        self._make_customer_project("acme-a1", self.customer, date(2026, 6, 1), date(2026, 6, 1))
        self._make_customer_project("acme-a2", self.customer, date(2026, 7, 1), date(2026, 7, 1))
        # Closed → not active → excluded.
        closed = self._make_customer_project("acme-closed", self.customer, date(2026, 6, 1), date(2026, 6, 1))
        closed.is_closed = True
        closed.save()
        # display_as_active=False → excluded.
        inactive = self._make_customer_project("acme-inactive", self.customer, date(2026, 6, 1), date(2026, 6, 1))
        inactive.display_as_active = False
        inactive.save()
        # Active project for a different customer must not inflate this customer's count.
        self._make_customer_project("globex-active", self.other_customer, date(2026, 6, 1), date(2026, 6, 1))

        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)
        customer = qs.get(pk=self.customer.pk)
        self.assertEqual(customer.active_project_count, 2)
        self.assertEqual(modeladmin.get_active_project_count(customer), 2)
        self.assertEqual(qs.get(pk=self.other_customer.pk).active_project_count, 1)


class KippoCustomerAdminComplianceDisplayTestCase(IsStaffModelAdminTestCaseBase):
    """KippoCustomerAdmin shows the 反社チェック (compliance verified) state as a boolean column."""

    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        super().setUp()
        self.customer = KippoCustomer.objects.create(
            organization=self.organization,
            name="Compliance Co",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

    def test_compliance_column_in_list_display(self):
        self.assertIn("get_compliance_verified", KippoCustomerAdmin.list_display)

    def test_compliance_display_method_is_boolean(self):
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        self.assertTrue(modeladmin.get_compliance_verified.boolean)

    def test_compliance_display_false_when_unverified(self):
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        # Auto-created compliance_check is unverified.
        self.assertFalse(modeladmin.get_compliance_verified(self.customer))

    def test_compliance_display_true_when_verified(self):
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        check = self.customer.compliance_check
        check.verified = True
        check.save()
        refreshed = KippoCustomer.objects.get(pk=self.customer.pk)
        self.assertTrue(modeladmin.get_compliance_verified(refreshed))

    def test_queryset_selects_related_compliance_check(self):
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)
        self.assertIn("compliance_check", qs.query.select_related)


class KippoCustomerAdminOrganizationFieldTestCase(IsStaffModelAdminTestCaseBase):
    """KippoCustomerAdmin initializes the organization from the user's session, and hides the
    field only for single-org users (multi-org users keep it visible to choose the org).
    """

    fixtures = DEFAULT_FIXTURES

    @staticmethod
    def _make_request(user: KippoUser, *, organization_id: str | None = None) -> MagicMock:
        request = MagicMock()
        request.user = user
        request.session = {"organization_id": organization_id} if organization_id else {}
        return request

    def test_get_form_hides_organization_field_and_initializes_to_session_org(self):
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        request = self._make_request(self.staffuser_with_org, organization_id=str(self.organization.id))
        form_class = modeladmin.get_form(request)
        self.assertIsInstance(form_class.base_fields["organization"].widget, forms.HiddenInput)
        self.assertEqual(form_class.base_fields["organization"].initial, self.organization)

    def test_get_form_does_not_hide_field_for_user_without_membership(self):
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        request = self._make_request(self.superuser_no_org)
        form_class = modeladmin.get_form(request)
        self.assertNotIsInstance(form_class.base_fields["organization"].widget, forms.HiddenInput)

    def test_get_form_keeps_organization_field_visible_for_multi_org_user(self):
        # Add the staff user to a second org so they belong to two organizations.
        OrganizationMembership.objects.create(
            user=self.staffuser_with_org,
            organization=self.other_organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        request = self._make_request(self.staffuser_with_org, organization_id=str(self.organization.id))
        form_class = modeladmin.get_form(request)
        # Multi-org → field stays visible, still initialized to the session org.
        self.assertNotIsInstance(form_class.base_fields["organization"].widget, forms.HiddenInput)
        self.assertEqual(form_class.base_fields["organization"].initial, self.organization)
