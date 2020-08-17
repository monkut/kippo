from django.test import TestCase, Client

from common.tests import IsStaffModelAdminTestCaseBase

from ..models import KippoUser, KippoOrganization, OrganizationMembership, PersonalHoliday
from ..admin import KippoUserAdmin, KippoOrganizationAdmin, OrganizationMembershipAdmin, PersonalHolidayAdmin


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
        modeladmin = OrganizationMembershipAdmin(OrganizationMembership, self.site)
        qs = list(modeladmin.get_queryset(self.super_user_request))
        # should list all
        expected = OrganizationMembership.objects.count()
        assert expected > 1
        actual = len(qs)
        msg = f'actual({actual}) != expected({expected})'
        self.assertTrue(actual == expected, msg)

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset = list(qs)
        expected_count = OrganizationMembership.objects.filter(organization__in=self.staff_user_request.user.organizations).count()
        self.assertTrue(
            len(queryset) == expected_count,
            f'actual({len(queryset)}) != expected({expected_count}): {queryset}'
        )

        staff_user_orgids = {o.id for o in self.staff_user_request.user.organizations}
        for membership in queryset:
            self.assertTrue(membership.organization.id in staff_user_orgids)

    def test_personalholidays_list_objects(self):
        modeladmin = PersonalHolidayAdmin(PersonalHoliday, self.site)
        qs = list(modeladmin.get_queryset(self.super_user_request))
        # should list all
        expected = PersonalHoliday.objects.count()
        assert expected > 1
        actual = len(qs)
        msg = f'actual({actual}) != expected({expected})'
        self.assertTrue(actual == expected, msg)

        # with staff user only single user with same org should be returned
        qs = modeladmin.get_queryset(self.staff_user_request)
        queryset = list(qs)
        expected_count = PersonalHoliday.objects.filter(user__organizationmembership__organization__in=self.staff_user_request.user.organizations).distinct().count()
        self.assertTrue(
            len(queryset) == expected_count,
            f'actual({len(queryset)}) != expected({expected_count}): {queryset}'
        )

        staff_user_orgids = {o.id for o in self.staff_user_request.user.organizations}
        for personalholiday in queryset:
            self.assertTrue(set(o.id for o in personalholiday.user.organizations).intersection(staff_user_orgids))

    def test_personalholidays_fields__is_staff(self):
        modeladmin = PersonalHolidayAdmin(PersonalHoliday, self.site)
        actual = modeladmin.get_fields(self.staff_user_request)
        expected = [
            'created_datetime',
            'is_half',
            'day',
            'duration'
        ]
        self.assertListEqual(actual, expected)

    def test_personalholidays__fields__is_superuser(self):
        modeladmin = PersonalHolidayAdmin(PersonalHoliday, self.site)
        actual = list(modeladmin.get_queryset(self.super_user_request))
        expected = [
            'created_datetime',
            'is_half',
            'day',
            'duration'
        ]
        self.assertListEqual(actual, expected)
