from commons.tests import IsStaffModelAdminTestCaseBase
from django import forms

from accounts.admin import PersonalHolidayAdmin
from accounts.models import PersonalHoliday


class AdminPublicHolidayTestCase(IsStaffModelAdminTestCaseBase):
    def test_form_return_correctly(self):
        modeladmin = PersonalHolidayAdmin(PersonalHoliday, self.site)

        # with super user all user selection should be provided
        user_widget = modeladmin.get_form(self.super_user_request).base_fields["user"].widget
        self.assertFalse(isinstance(user_widget, forms.HiddenInput))
        self.assertIsNone(modeladmin.get_form(self.super_user_request).base_fields["user"].initial)

        # with staff user user selection should be hidden and user should be set as him/herself
        user_widget = modeladmin.get_form(self.staff_user_request).base_fields["user"].widget
        self.assertTrue(isinstance(user_widget, forms.HiddenInput))
        self.assertEqual(modeladmin.get_form(self.staff_user_request).base_fields["user"].initial, self.staff_user_request.user)

    def test_personalholidays_list_objects_by_super_user(self):
        modeladmin = PersonalHolidayAdmin(PersonalHoliday, self.site)
        qs = list(modeladmin.get_queryset(self.super_user_request))
        # should list all
        expected = PersonalHoliday.objects.count()
        assert expected > 1
        self.assertTrue(expected, len(qs))

    def test_personalholidays_list_objects_by_staff_user_with_no_org(self):
        modeladmin = PersonalHolidayAdmin(PersonalHoliday, self.site)
        qs = list(modeladmin.get_queryset(self.staff_user2_request))
        # only him/herself should be returned
        expected = PersonalHoliday.objects.filter(user=self.staff_user2_request.user).count()
        self.assertTrue(expected, len(qs))

    def test_personalholidays_list_objects_by_staff_user_with_org(self):
        modeladmin = PersonalHolidayAdmin(PersonalHoliday, self.site)
        qs = list(modeladmin.get_queryset(self.staff_user_request))
        # only single user with same org should be returned
        expected = (
            PersonalHoliday.objects.filter(user__organizationmembership__organization__in=self.staff_user_request.user.organizations)
            .distinct()
            .count()
        )
        self.assertTrue(expected, len(qs))

        staff_user_orgids = {o.id for o in self.staff_user_request.user.organizations}
        for personalholiday in qs:
            self.assertTrue(set(o.id for o in personalholiday.user.organizations).intersection(staff_user_orgids))
