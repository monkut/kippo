from django.test import TestCase

from ..models import KippoOrganization, KippoUser, OrganizationMembership


class KippoOrganizationTestCase(TestCase):
    fixtures = [
        "default_columnset",
        "required_bot_users",
    ]

    def setUp(self):
        self.github_manager_user = KippoUser.objects.get(username="github-manager")

    def create_organization_unassigned_kippouser(self):
        org_name = "testorg1"
        dummy_organization = KippoOrganization(
            name=org_name,
            github_organization_name="ghdummyorg",
            created_by=self.github_manager_user,
            updated_by=self.github_manager_user,
        )
        dummy_organization.save()

        expected_github_unassigned_username = f"github-unassigned-{org_name}"
        actual_candidates = KippoUser.objects.filter(username=expected_github_unassigned_username)
        self.assertTrue(actual_candidates)
        actual = actual_candidates[0]
        self.assertTrue(actual.username == expected_github_unassigned_username)

        # check for organization membership
        candidate_memberships = OrganizationMembership.objects.filter(
            organization=dummy_organization,
            user=actual,
        )
        self.assertTrue(candidate_memberships)
        self.assertTrue(len(candidate_memberships) == 1)

        unassigned_user = dummy_organization.get_unassigned_kippouser()
        self.assertTrue(unassigned_user)
