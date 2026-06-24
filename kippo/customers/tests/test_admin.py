from datetime import date
from http import HTTPStatus
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

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

    def _make_customer_project(self, name: str, customer: KippoCustomer, target_date: date) -> KippoProject:
        return KippoProject.objects.create(
            organization=self.organization,
            name=name,
            category=default_project_category(),
            columnset=self.columnset,
            customer=customer,
            target_date=target_date,
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
        for fieldname in ("get_project_link", "start_date", "target_date"):
            self.assertIn(fieldname, inline.fields)

    def test_change_view_lists_related_project_with_admin_link(self):
        project = self._make_customer_project("acme-alpha", self.customer, date(2026, 6, 1))
        url = reverse("admin:customers_kippocustomer_change", args=[self.customer.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        content = response.content.decode()
        self.assertIn(project.name, content)
        self.assertIn(project.get_admin_url(), content)

    def test_change_view_orders_projects_by_target_date_ascending(self):
        later = self._make_customer_project("acme-later", self.customer, date(2026, 9, 1))
        earlier = self._make_customer_project("acme-earlier", self.customer, date(2026, 3, 1))
        url = reverse("admin:customers_kippocustomer_change", args=[self.customer.id])
        content = self.client.get(url).content.decode()
        self.assertLess(content.index(earlier.name), content.index(later.name))

    def test_change_view_excludes_other_customers_projects(self):
        mine = self._make_customer_project("acme-mine", self.customer, date(2026, 6, 1))
        theirs = self._make_customer_project("globex-theirs", self.other_customer, date(2026, 6, 1))
        url = reverse("admin:customers_kippocustomer_change", args=[self.customer.id])
        content = self.client.get(url).content.decode()
        self.assertIn(mine.name, content)
        self.assertNotIn(theirs.name, content)

    def test_change_view_shows_add_project_button_prefilled_with_customer_and_return(self):
        url = reverse("admin:customers_kippocustomer_change", args=[self.customer.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        add_project_url = response.context["add_project_url"]
        parsed = urlparse(add_project_url)
        self.assertEqual(parsed.path, reverse("admin:projects_activekippoproject_add"))
        params = parse_qs(parsed.query)
        self.assertEqual(params["customer"], [str(self.customer.id)])
        self.assertEqual(params["organization"], [str(self.organization.id)])
        self.assertEqual(params["_return_to"], [url])
        # button is rendered (label + link to the project add view)
        content = response.content.decode()
        self.assertIn("プロジェクトを追加", content)
        self.assertIn(reverse("admin:projects_activekippoproject_add"), content)

    def test_add_view_has_no_add_project_button(self):
        # No customer pk yet on add → no project can be linked, so the button is absent.
        response = self.client.get(reverse("admin:customers_kippocustomer_add"))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertIsNone(response.context.get("add_project_url"))
        self.assertNotIn("プロジェクトを追加", response.content.decode())

    def test_changelist_active_project_count_excludes_closed_and_inactive(self):
        # Two active projects (is_closed=False, display_as_active=True by default).
        self._make_customer_project("acme-a1", self.customer, date(2026, 6, 1))
        self._make_customer_project("acme-a2", self.customer, date(2026, 7, 1))
        # Closed → not active → excluded.
        closed = self._make_customer_project("acme-closed", self.customer, date(2026, 6, 1))
        closed.is_closed = True
        closed.save()
        # display_as_active=False → excluded.
        inactive = self._make_customer_project("acme-inactive", self.customer, date(2026, 6, 1))
        inactive.display_as_active = False
        inactive.save()
        # Active project for a different customer must not inflate this customer's count.
        self._make_customer_project("globex-active", self.other_customer, date(2026, 6, 1))

        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)
        customer = qs.get(pk=self.customer.pk)
        self.assertEqual(customer.active_project_count, 2)
        # non-zero count renders the clickable toggle showing the count
        rendered = modeladmin.get_active_project_count(customer)
        self.assertIn("active-projects-toggle", rendered)
        self.assertIn(">2<", rendered)
        self.assertEqual(qs.get(pk=self.other_customer.pk).active_project_count, 1)

    def test_active_project_count_zero_renders_plain(self):
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)
        customer = qs.get(pk=self.customer.pk)  # no projects in this test
        self.assertEqual(modeladmin.get_active_project_count(customer), 0)

    def test_fiscal_year_summary_header_counts_contracts_ending_this_fy(self):
        from datetime import timedelta
        from decimal import Decimal

        from django.utils import timezone
        from projects.models import KippoProjectBillingEntry, KippoProjectContract

        # Fiscal year starts in January → current FY = this (JST) calendar year.
        self.organization.fiscalyear_start_month = 1
        self.organization.save()
        today = timezone.localdate()
        fy_start = date(today.year, 1, 1)

        # contract ending THIS FY → counted; planned = total_amount; received = its received entries
        in_fy = self._make_customer_project("in-fy", self.customer, date(today.year, 6, 30))
        in_fy_contract = KippoProjectContract.objects.create(
            project=in_fy,
            billing_type="delivery",
            total_amount=Decimal("2000000"),
            end_date=date(today.year, 6, 30),
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        KippoProjectBillingEntry.objects.create(
            contract=in_fy_contract, billing_date=date(today.year, 6, 30), amount=Decimal("500000"), is_received=True
        )
        # contract ending in a PRIOR FY → excluded from count / planned / received
        prior = self._make_customer_project("prior-fy", self.customer, fy_start - timedelta(days=1))
        KippoProjectContract.objects.create(
            project=prior,
            billing_type="delivery",
            total_amount=Decimal("9000000"),
            end_date=fy_start - timedelta(days=1),
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )

        response = self.client.get(reverse("admin:customers_kippocustomer_changelist"))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        # header summary excludes the prior-FY contract: count=1, planned=2,000,000 (not +9,000,000)
        summary = next(s for s in response.context["fiscal_year_summaries"] if s["organization"] == self.organization.name)
        self.assertEqual(summary["project_count"], 1)  # only the contract ending this FY
        self.assertEqual(summary["planned_total_display"], "¥2,000,000")
        self.assertEqual(summary["received_total_display"], "¥500,000")
        self.assertIn("契約予定合計", response.content.decode())  # header block rendered

    def test_active_project_detail_sums_received_within_current_fiscal_year(self):
        from datetime import timedelta
        from decimal import Decimal

        from django.utils import timezone
        from projects.models import KippoProjectBillingEntry, KippoProjectContract

        # Fiscal year starts in January for this org → cutoff = Jan 1 of the current (JST) year.
        self.organization.fiscalyear_start_month = 1
        self.organization.save()
        today = timezone.localdate()
        fy_start = date(today.year, 1, 1)

        project = self._make_customer_project("acme-billed", self.customer, date(today.year, 9, 30))
        contract = KippoProjectContract.objects.create(
            project=project,
            billing_type="delivery",
            total_amount=Decimal("2000000"),
            end_date=date(today.year, 9, 30),
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        # received, on/after the fiscal-year start → counted
        KippoProjectBillingEntry.objects.create(contract=contract, billing_date=fy_start, amount=Decimal("500000"), is_received=True)
        # received, but BEFORE the fiscal-year start (prior FY) → excluded by the cutoff
        KippoProjectBillingEntry.objects.create(
            contract=contract, billing_date=fy_start - timedelta(days=1), amount=Decimal("900000"), is_received=True
        )
        # not received, within the FY → excluded (only received entries are prefetched)
        KippoProjectBillingEntry.objects.create(
            contract=contract, billing_date=fy_start + timedelta(days=10), amount=Decimal("1500000"), is_received=False
        )

        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        customer = modeladmin.get_queryset(self.super_user_request).get(pk=self.customer.pk)
        rendered = modeladmin.get_active_project_count(customer)
        self.assertIn("acme-billed", rendered)
        self.assertIn("¥500,000", rendered)  # only the in-FY received entry
        self.assertNotIn("¥1,400,000", rendered)  # the pre-FY received entry is NOT added in
        self.assertIn("¥2,000,000", rendered)  # contract total_amount
        self.assertIn(date(today.year, 9, 30).isoformat(), rendered)  # contract end date

    def test_recent_ending_customer_filter_lists_only_customers_with_projects_ending_in_last_2_fy(self):
        from datetime import timedelta
        from decimal import Decimal

        from django.utils import timezone
        from projects.models import KippoProjectContract

        from customers.admin import CustomerEndingProjectsFilter

        self.organization.fiscalyear_start_month = 1
        self.organization.save()
        today = timezone.localdate()
        fy_start = date(today.year, 1, 1)

        def _contract(project: KippoProject, end_date: date) -> None:
            KippoProjectContract.objects.create(
                project=project,
                billing_type="delivery",
                total_amount=Decimal("1000000"),
                end_date=end_date,
                created_by=self.github_manager,
                updated_by=self.github_manager,
            )

        # self.customer: a project whose contract ends this FY → qualifies
        _contract(self._make_customer_project("recent", self.customer, date(today.year, 6, 30)), date(today.year, 6, 30))
        # self.other_customer: only a contract ending 2 FYs before the current one → excluded
        _contract(
            self._make_customer_project("old", self.other_customer, fy_start - timedelta(days=400)),
            fy_start - timedelta(days=400),
        )

        flt = CustomerEndingProjectsFilter(self.super_user_request, {}, KippoCustomer, KippoCustomerAdmin(KippoCustomer, self.site))
        names = {name for _pk, name in flt.lookups(self.super_user_request, None)}
        self.assertIn(self.customer.name, names)
        self.assertNotIn(self.other_customer.name, names)

        # selecting a customer filters the changelist to it
        url = reverse("admin:customers_kippocustomer_changelist") + f"?recent_ending_customer={self.customer.pk}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        results = response.context["cl"].queryset
        self.assertIn(self.customer, results)
        self.assertNotIn(self.other_customer, results)

    def test_visible_organizations_scope_matches_changelist(self):
        # the FY header and the name filter use the same org scope as the rows: all orgs for a
        # superuser (changelist is not org-scoped for superusers), else the user's own orgs.
        from accounts.models import KippoOrganization

        from customers.admin import _visible_organizations

        self.assertEqual(set(_visible_organizations(self.super_user_request.user)), set(KippoOrganization.objects.all()))
        staff_user = self.staff_user_request.user
        self.assertEqual(set(_visible_organizations(staff_user)), set(staff_user.organizations))

    def test_current_fiscal_year_start_uses_organization_timezone(self):
        import datetime as dt
        from unittest.mock import patch

        from customers.admin import _current_fiscal_year_start

        self.organization.fiscalyear_start_month = 1
        # An instant that is 2026-01-01 in Tokyo (+09:00) but still 2025-12-31 in Los Angeles (-08:00).
        instant = dt.datetime(2026, 1, 1, 2, 0, tzinfo=dt.UTC)
        with patch("django.utils.timezone.now", return_value=instant):
            self.organization.timezone = "Asia/Tokyo"
            self.assertEqual(_current_fiscal_year_start(self.organization), dt.date(2026, 1, 1))
            self.organization.timezone = "America/Los_Angeles"
            self.assertEqual(_current_fiscal_year_start(self.organization), dt.date(2025, 1, 1))

    def test_current_fiscal_year_start_falls_back_to_jst_on_invalid_timezone(self):
        # validate_timezone runs only on full_clean, not .save(); a bad value must not 500 the
        # changelist — fall back to JST.
        import datetime as dt
        from unittest.mock import patch

        from customers.admin import _current_fiscal_year_start

        self.organization.fiscalyear_start_month = 1
        instant = dt.datetime(2026, 1, 1, 2, 0, tzinfo=dt.UTC)  # 2026-01-01 in JST
        with patch("django.utils.timezone.now", return_value=instant):
            for bad in ("", "Not/AZone"):
                self.organization.timezone = bad
                self.assertEqual(_current_fiscal_year_start(self.organization), dt.date(2026, 1, 1))


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


class KippoCustomerAdminListDisplayTestCase(IsStaffModelAdminTestCaseBase):
    """The changelist drops the email column, orders by active project count, and shows the
    organization column only for multi-org superusers.
    """

    fixtures = DEFAULT_FIXTURES

    @staticmethod
    def _request(user: KippoUser) -> MagicMock:
        request = MagicMock()
        request.user = user
        return request

    def test_email_not_in_list_display(self):
        self.assertNotIn("email", KippoCustomerAdmin.list_display)

    def test_ordering_by_active_project_count_descending(self):
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        ordering = modeladmin.get_ordering(self._request(self.superuser_no_org))
        # most active first (descending), then name
        self.assertTrue(ordering[0].descending)
        self.assertEqual(ordering[1], "name")

    def test_changelist_orders_customers_by_active_project_count(self):
        # end-to-end: the rendered changelist must order rows by active project count, descending.
        from datetime import date

        from commons.tests import DEFAULT_COLUMNSET_PK
        from projects.models import ProjectColumnSet

        columnset = ProjectColumnSet.objects.get(pk=DEFAULT_COLUMNSET_PK)

        def _customer(name: str) -> KippoCustomer:
            return KippoCustomer.objects.create(
                organization=self.organization, name=name, created_by=self.github_manager, updated_by=self.github_manager
            )

        busy = _customer("Busy")
        idle = _customer("Idle")
        for i in range(2):
            KippoProject.objects.create(
                organization=self.organization,
                name=f"busy-project-{i}",
                columnset=columnset,
                start_date=date(2026, 6, 1),
                customer=busy,
                created_by=self.github_manager,
                updated_by=self.github_manager,
            )
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        request = self._request(self.superuser_no_org)
        ordered = list(modeladmin.get_queryset(request).order_by(*modeladmin.get_ordering(request)))
        self.assertLess(ordered.index(busy), ordered.index(idle))

    def test_organization_hidden_for_non_superuser(self):
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        self.assertNotIn("organization", modeladmin.get_list_display(self._request(self.staffuser_with_org)))

    def test_organization_hidden_for_single_org_superuser(self):
        OrganizationMembership.objects.create(
            user=self.superuser_no_org,
            organization=self.organization,
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        self.assertNotIn("organization", modeladmin.get_list_display(self._request(self.superuser_no_org)))

    def test_organization_shown_for_multi_org_superuser(self):
        for organization in (self.organization, self.other_organization):
            OrganizationMembership.objects.create(
                user=self.superuser_no_org,
                organization=organization,
                created_by=self.github_manager,
                updated_by=self.github_manager,
            )
        modeladmin = KippoCustomerAdmin(KippoCustomer, self.site)
        list_display = modeladmin.get_list_display(self._request(self.superuser_no_org))
        self.assertIn("organization", list_display)
        # organization sits immediately after name
        self.assertEqual(list_display[:2], ("name", "organization"))
