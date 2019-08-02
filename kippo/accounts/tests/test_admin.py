from common.tests import IsStaffModelAdminTestCaseBase

from ..models import KippoUser, KippoOrganization, OrganizationMembership
from ..admin import KippoUserAdmin, KippoOrganizationAdmin


class IsStaffOrganizationKippoUserModelAdminTestCase(IsStaffModelAdminTestCaseBase):

    def test_users_list_objects(self):
        modeladmin = KippoUserAdmin(KippoUser, self.site)
        qs = modeladmin.get_queryset(self.super_user_request)

        # should list all users
        all_users_count = KippoUser.objects.count()
        self.assertTrue(all_users_count == len(qs))

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset_users = list(qs)
        expected_user_count = len({m.user.id for m in OrganizationMembership.objects.filter(organization__in=self.staff_user_request.user.organizations)})
        self.assertTrue(
            len(queryset_users) == expected_user_count,
            f'actual({len(queryset_users)}) != expected({expected_user_count}): {", ".join(u.username for u in queryset_users)}'
        )

        staff_user_orgids = {o.id for o in self.staff_user_request.user.organizations}
        for queryset_user in queryset_users:
            queryset_user_orgids = {o.id for o in queryset_user.organizations}
            self.assertTrue(staff_user_orgids.intersection(queryset_user_orgids))

    def test_kippoorganization_list_objects(self):
        modeladmin = KippoOrganizationAdmin(KippoOrganization, self.site)
        qs = list(modeladmin.get_queryset(self.super_user_request))
        # should list all users
        expected = KippoOrganization.objects.count()
        assert expected > 1
        actual = len(qs)
        self.assertTrue(actual == expected, f'actual({actual})[{", ".join(o.name for o in qs)}] != expected({expected})[{", ".join(o.name for o in  KippoOrganization.objects.all())}]')

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset_orgs = list(qs)
        expected_org_count = len({m.organization.id for m in OrganizationMembership.objects.filter(organization__in=self.staff_user_request.user.organizations)})
        self.assertTrue(
            len(queryset_orgs) == expected_org_count,
            f'actual({len(queryset_orgs)}) != expected({expected_org_count}): {", ".join(o.name for o in queryset_orgs)}'
        )

        staff_user_orgids = {o.id for o in self.staff_user_request.user.organizations}
        for queryset_org in queryset_orgs:
            self.assertTrue(queryset_org.id in staff_user_orgids)

    def test_organizationmemberships_list_objects(self):
        raise NotImplementedError

    def test_personalholidays_list_objects(self):
        raise NotImplementedError

    def test_publicholidays_list_objects(self):
        raise NotImplementedError

    def test_contry_list_objects(self):
        raise NotImplementedError
#
# class AdminTestCase(TestCase):
#
#     def setUp(self):
#         super().setUp()
#
#         superuser = create_user()
#         superuser.is_staff = True
#         superuser.is_superuser = True
#         superuser.organization = None
#         superuser.save()
#
#         organization = AnnotationOrganization.objects.create(name='test-org')
#         staff = create_user()
#         staff.is_staff = True
#         staff.is_superuser = False
#         staff.organization = organization
#         staff.save()
#         self.staff_user = staff
#
#         self.superuser = superuser
#         self.superuser_client = Client()
#         self.superuser_client.force_login(self.superuser)
#
#         self.staff = staff
#         self.staff_client = Client()
#         self.staff_client.force_login(self.staff)
#
#     def test_annotationorganizationadmin(self):
#         url = '/admin/accounts/annotationorganization/'
#         response = self.superuser_client.get(url)
#         self.assertTrue(response.status_code == 200)
#
#         response = self.staff_client.get(url)
#         self.assertTrue(response.status_code == 403)
#
#     def test_annotationuseradmin(self):
#         url = '/admin/accounts/annotationuser/'
#         response = self.superuser_client.get(url)
#         self.assertTrue(response.status_code == 200)
#
#         response = self.staff_client.get(url)
#         self.assertTrue(response.status_code == 403)
#
#     def test_organizationannotationuseradmin(self):
#         url = '/admin/accounts/organizationannotationuser/'
#         response = self.superuser_client.get(url)
#         self.assertTrue(response.status_code == 200)
#
#         response = self.staff_client.get(url)
#         self.assertTrue(response.status_code == 200)
#
#     def test_logentryadmin(self):
#         url = '/admin/admin/logentry/'
#         response = self.superuser_client.get(url)
#         self.assertTrue(response.status_code == 200)
#
#         response = self.staff_client.get(url)
#         self.assertTrue(response.status_code == 403)
#
#     def test_annotationuseradmin_isstaff_revoke_action(self):
#         user = AnnotationUser.objects.get(pk=self.staff_user.pk)
#         assert user.is_staff == True
#
#         data = {'action': 'revoke_isstaff',
#                 '_selected_action': [str(self.staff_user.pk)]}
#         change_url = reverse('admin:accounts_annotationuser_changelist')
#         response = self.superuser_client.post(change_url, data, follow=True)
#         self.assertTrue(response.status_code == 200)
#
#         user = AnnotationUser.objects.get(pk=self.staff_user.pk)
#         self.assertTrue(user.is_staff == False)
#
#         # repeat with is_staff user, make sure they don't have permissions
#         # --> revert user back to is_staff = True
#         user.is_staff = True
#         user.save()
#
#         data = {'action': 'revoke_isstaff',
#                 '_selected_action': [str(self.staff_user.pk)]}
#         change_url = reverse('admin:accounts_annotationuser_changelist')
#         response = self.staff_client.post(change_url, data, follow=True)
#         self.assertTrue(response.status_code == 403)
#
#
