from accounts.models import KippoUser
from commons.tests import DEFAULT_FIXTURES, setup_basic_project
from django.test import TestCase
from projects.models import KippoProject

from customers.models import KippoCustomer


class KippoCustomerModelTestCase(TestCase):
    fixtures = DEFAULT_FIXTURES

    def setUp(self):
        created = setup_basic_project()
        self.organization = created["KippoOrganization"]
        self.user = created["KippoUser"]
        self.project = created["KippoProject"]
        self.github_manager = KippoUser.objects.get(username="github-manager")

    def test_create_and_assign_customer_to_project(self):
        customer = KippoCustomer.objects.create(
            organization=self.organization,
            name="Acme Corp",
            email="contact@acme.example.com",
            created_by=self.user,
            updated_by=self.user,
        )
        self.project.customer = customer
        self.project.save()

        refreshed = KippoProject.objects.get(pk=self.project.pk)
        self.assertEqual(refreshed.customer, customer)
        self.assertEqual(refreshed.customer.name, "Acme Corp")

    def test_unique_together_organization_name_enforced(self):
        KippoCustomer.objects.create(
            organization=self.organization,
            name="Acme Corp",
            created_by=self.user,
            updated_by=self.user,
        )
        with self.assertRaises(Exception) as raised:  # IntegrityError
            KippoCustomer.objects.create(
                organization=self.organization,
                name="Acme Corp",
                created_by=self.user,
                updated_by=self.user,
            )
        self.assertIn("unique", str(raised.exception).lower())

    def test_same_name_in_different_organizations_allowed(self):
        other_organization = self.organization.__class__.objects.create(
            name="other-org-for-dup",
            github_organization_name="other-org-for-dup",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        KippoCustomer.objects.create(
            organization=self.organization,
            name="Globex",
            created_by=self.user,
            updated_by=self.user,
        )
        # Same name in a different org is allowed
        other_customer = KippoCustomer.objects.create(
            organization=other_organization,
            name="Globex",
            created_by=self.github_manager,
            updated_by=self.github_manager,
        )
        self.assertIsNotNone(other_customer.pk)

    def test_deleting_customer_sets_project_customer_to_null(self):
        customer = KippoCustomer.objects.create(
            organization=self.organization,
            name="To Delete",
            created_by=self.user,
            updated_by=self.user,
        )
        self.project.customer = customer
        self.project.save()

        customer_id = customer.id
        customer.delete()

        refreshed_project = KippoProject.objects.get(pk=self.project.pk)
        self.assertIsNone(refreshed_project.customer)
        self.assertFalse(KippoCustomer.objects.filter(pk=customer_id).exists())

    def test_customer_field_defaults_on_kippoproject(self):
        field = KippoProject._meta.get_field("customer")
        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        self.assertEqual(field.related_model, KippoCustomer)

    def test_document_url_defaults_to_blank_and_persists(self):
        default_customer = KippoCustomer.objects.create(
            organization=self.organization,
            name="DocDefaults",
            created_by=self.user,
            updated_by=self.user,
        )
        self.assertEqual(default_customer.document_url, "")

        url = "https://example.com/customers/acme/docs"
        customer = KippoCustomer.objects.create(
            organization=self.organization,
            name="DocSet",
            document_url=url,
            created_by=self.user,
            updated_by=self.user,
        )
        refreshed = KippoCustomer.objects.get(pk=customer.pk)
        self.assertEqual(refreshed.document_url, url)
