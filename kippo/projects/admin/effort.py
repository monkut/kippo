"""Weekly effort and weekly effort unlock admins."""

import urllib.parse
from collections import Counter, defaultdict
from collections.abc import Iterable

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership, PublicHoliday
from commons.admin import AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin
from commons.definitions import SATURDAY
from commons.functions import get_current_month_date_range
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import QuerySet
from django.forms import Form
from django.http import (
    HttpRequest,
    HttpResponseRedirect,
    request as DjangoRequest,  # noqa: N812
)
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rangefilter.filters import DateRangeFilterBuilder

from ..definitions import (
    WEEKLY_EFFORT_CLOSED_MESSAGE,
)
from ..functions import (
    generate_projectmonthlyeffort_csv,
    generate_projectweeklyeffort_csv,
    get_user_session_organization,
)
from ..models import (
    KippoProject,
    ProjectWeeklyEffort,
    ProjectWeeklyEffortUnlock,
)


class ProjectWeeklyEffortAdminForm(forms.ModelForm):
    """Blocks non-superusers from creating/moving effort into a closed week (kippo#33 / T17).

    `request_user` is set per-request by ProjectWeeklyEffortAdmin.get_form.
    """

    request_user = None

    class Meta:
        model = ProjectWeeklyEffort
        fields = "__all__"  # noqa: DJ007

    def clean(self):
        cleaned = super().clean()
        if self.request_user is None or self.request_user.is_superuser:
            return cleaned
        project = cleaned.get("project")
        week_start = cleaned.get("week_start")
        effort_user = cleaned.get("user")
        if project and week_start and effort_user and project.organization.is_weeklyeffort_closed(effort_user, week_start):
            raise ValidationError(WEEKLY_EFFORT_CLOSED_MESSAGE, code="weekly_effort_closed")
        return cleaned


@admin.register(ProjectWeeklyEffort)
class ProjectWeeklyEffortAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    form = ProjectWeeklyEffortAdminForm
    change_list_template = "admin/projects/weeklyeffortadmin.html"
    list_display = ("get_project_name", "week_start", "get_user_display_name", "hours")
    ordering = ("project", "-week_start", "user")
    search_fields = (
        "project__name",
        "user__last_name",
    )
    actions = ("download_csv", "download_monthly_csv")

    def has_change_permission(self, request: DjangoRequest, obj: ProjectWeeklyEffort | None = None) -> bool:
        if obj and not request.user.is_superuser and obj.is_closed():
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request: DjangoRequest, obj: ProjectWeeklyEffort | None = None) -> bool:
        if obj and not request.user.is_superuser and obj.is_closed():
            return False
        return super().has_delete_permission(request, obj)

    def get_list_filter(self, request: DjangoRequest) -> list:
        current_month_start, current_month_end = get_current_month_date_range()
        return [
            ("week_start", DateRangeFilterBuilder(title="date filter", default_start=current_month_start, default_end=current_month_end)),
        ]

    def get_project_name(self, obj: ProjectWeeklyEffort | None = None) -> str:
        result = "-"
        if obj and obj.project and obj.project.name:
            result = obj.project.name
        return result

    get_project_name.short_description = _("Project")

    def get_user_display_name(self, obj: ProjectWeeklyEffort | None = None) -> str:
        result = "-"
        if obj:
            result = obj.user.display_name
        return result

    get_user_display_name.short_description = _("user")

    @admin.action(description=_("Download ProjectWeeklyEffort CSV"))
    def download_csv(self, request: DjangoRequest, queryset: models.QuerySet) -> HttpResponseRedirect | None:
        if not ProjectWeeklyEffort.objects.filter(project__organization__in=request.user.organizations).exists():
            self.message_user(request, _("No ProjectWeeklyEffort exists!"), level=messages.WARNING)
            return None
        # initiate creation
        now = timezone.now()
        filename = now.strftime("project-effort-%Y%m%d%H%M%S.csv")
        key = f"tmp/download/{filename}"
        selected_query_id = list(queryset.values_list("id", flat=True))
        generate_projectweeklyeffort_csv(user_id=str(request.user.pk), key=key, effort_ids=selected_query_id)
        # redirect to waiter
        urlencoded_key = urllib.parse.quote_plus(key)
        download_waiter_url = f"{settings.URL_PREFIX}/projects/download/?filename={urlencoded_key}"
        return HttpResponseRedirect(redirect_to=download_waiter_url)

    @admin.action(description="Download ProjectMonthlyEffort CSV")
    def download_monthly_csv(self, request: DjangoRequest, queryset: models.QuerySet):
        if not ProjectWeeklyEffort.objects.filter(project__organization__in=request.user.organizations).exists():
            self.message_user(request, _("No ProjectWeeklyEffort exists!"), level=messages.WARNING)
            return None
        # initiate creation
        now = timezone.localtime()
        filename = now.strftime("project-monthly-effort-%Y%m%d%H%M%S.csv")
        key = f"tmp/download/{filename}"
        selected_query_id = list(queryset.values_list("id", flat=True))
        generate_projectmonthlyeffort_csv(user_id=str(request.user.pk), key=key, effort_ids=selected_query_id)
        # redirect to waiter
        urlencoded_key = urllib.parse.quote_plus(key)
        download_waiter_url = f"{settings.URL_PREFIX}/projects/download/?filename={urlencoded_key}"
        return HttpResponseRedirect(redirect_to=download_waiter_url)

    def save_model(self, request: DjangoRequest, obj: ProjectWeeklyEffort, form: Form, change: bool):
        """Force the entry's user to the requesting user unless they are a superuser.

        Guards against a tampered submission setting a different `user`; the get_form widget below
        only hides the field in the UI for non-superusers.
        """
        if not request.user.is_superuser:
            obj.user = request.user
        super().save_model(request, obj, form, change)

    def get_form(self, request: DjangoRequest, obj: ProjectWeeklyEffort | None = None, **kwargs):
        """Set defaults based on request user"""
        # update user field with logged user as default
        form = super().get_form(request, obj, **kwargs)
        form.request_user = request.user  # consumed by ProjectWeeklyEffortAdminForm.clean (kippo#33 / T17)
        form.base_fields["user"].initial = request.user.id
        try:
            user_initial_organization, user_organizations = get_user_session_organization(request)
            user_memberships = request.user.memberships.all()
        except ValueError:
            user_initial_organization = None
            user_memberships = request.user.memberships.none()
        if not user_initial_organization:
            self.message_user(
                request,
                "OrganizationMembership not defined for user! Must belong to an Organization to create a project",
                level=messages.ERROR,
            )
        if request.user.is_superuser:
            # Superusers may log effort for any user belonging to an organization they are a member of.
            member_user_ids = OrganizationMembership.objects.filter(organization__in=user_memberships).values_list("user__id", flat=True)
            form.base_fields["user"].queryset = KippoUser.objects.filter(id__in=member_user_ids).order_by("last_name", "username")
        else:
            # Non-superusers may only log their own effort, so the user field is hidden and forced
            # to the requesting user (see save_model).
            form.base_fields["user"].widget = forms.HiddenInput()
        user_projects = KippoProject.objects.filter(organization__in=user_memberships)
        form.base_fields["project"].initial = user_projects.first()
        form.base_fields["project"].queryset = user_projects
        return form

    def get_queryset(self, request: DjangoRequest) -> models.QuerySet:
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(project__organization__in=request.user.organizations).order_by("project__organization")

    def get_fiscal_year_org_per_user_weeklyeffort(self, organizations: Iterable[KippoOrganization]) -> tuple:  # noqa: C901, PLR0912
        all_months = set()
        monthly_expected_hours = Counter()

        monthly_week_starts = []
        results = {}
        now = timezone.now()
        monthly_expected_hours_processed = False
        organizations = list(organizations)
        if not organizations:
            # `current_fiscal_year` and `org` are set in the loop below and reused after it
            # (public-holiday subtraction); a user with no organization (superuser) has no summary.
            return {}, {}, ()
        for org in organizations:
            user_weekstarts = defaultdict(list)
            results[org.name] = {}
            if now.month < org.fiscalyear_start_month:
                current_fiscal_year = timezone.datetime(now.year - 1, org.fiscalyear_start_month, 1, tzinfo=timezone.timezone.utc)
            else:
                current_fiscal_year = timezone.datetime(now.year, org.fiscalyear_start_month, 1, tzinfo=timezone.timezone.utc)
            # get organization users
            # -- aggregate projectweeklyeffort per user per month
            users = org.get_membership_kippousers()
            projectweeklyeffort = ProjectWeeklyEffort.objects.filter(
                user__in=users, week_start__gte=current_fiscal_year.date(), project__organization=org
            )
            sum_index = 0
            flag_index = 1
            for effort in projectweeklyeffort:
                if effort.user.username not in results[org.name]:
                    results[org.name][effort.user.username] = {}
                if effort.week_start.month not in results[org.name][effort.user.username]:
                    results[org.name][effort.user.username][effort.week_start.month] = [0, False]
                results[org.name][effort.user.username][effort.week_start.month][sum_index] += effort.hours
                user_weekstarts[effort.user.username].append(effort.week_start)

            # remove public holidays from total
            # -- calculate total workdays from fiscal start
            if not monthly_expected_hours:
                current = current_fiscal_year
                while current <= now:
                    all_months.add(current.month)
                    if current.weekday() < SATURDAY:  # SAT=5, SUN=6
                        monthly_expected_hours[current.month] += 1
                    if current.weekday() == 0:
                        monthly_week_starts.append(current.date())
                    current += timezone.timedelta(days=1)
            # apply hours
            for month in monthly_expected_hours:
                if not monthly_expected_hours_processed:
                    monthly_expected_hours[month] *= org.day_workhours
                # -- update user dictionaries with 0s
                for org_user_info in results.values():
                    for user_month_data in org_user_info.values():
                        if month and month not in user_month_data:
                            user_month_data[month] = [0, False]
                        elif (
                            user_month_data[month][sum_index]
                            > monthly_expected_hours[month] + monthly_expected_hours[month] * settings.PROJECT_EFFORT_EXCEED_PERCENTAGE
                        ):
                            user_month_data[month][flag_index] = True
            monthly_expected_hours_processed = True
            # re-sort user_data
            for org_key in results:  # noqa: PLC0206
                for user_key in results[org_key].keys():  # noqa: PLC0206
                    if "missing" not in results[org_key][user_key]:
                        this_week_start = now
                        while this_week_start.weekday() != 0:
                            this_week_start -= timezone.timedelta(days=1)

                        results[org_key][user_key] = dict(sorted(results[org_key][user_key].items()))
                        # add missing
                        user_missing_weekstarts = set(monthly_week_starts) - set(user_weekstarts[user_key])
                        results[org_key][user_key]["missing"] = [
                            ", ".join(d.strftime("%m-%d") for d in sorted(user_missing_weekstarts) if d != this_week_start.date()),
                            False,
                        ]  # noqa: PLC0206

        # -- calculate public holidays
        for holiday in PublicHoliday.objects.filter(day__gte=current_fiscal_year.date(), day__lte=now):
            # -- subtract public holidays from current total
            monthly_expected_hours[holiday.day.month] -= 1 * org.day_workhours

        return dict(results), dict(monthly_expected_hours), tuple(all_months)

    def changelist_view(self, request: DjangoRequest, extra_context: dict | None = None):
        # Add the fiscal-year summary to the changelist context and let Django render it with
        # `change_list_template`. Action responses (e.g. the delete_selected confirmation page,
        # which has no `cl` in its context) pass through untouched instead of being re-rendered
        # with the changelist template, which raised NoReverseMatch for admin:app_list -> 500.
        summary_results, expected_hours, all_months = self.get_fiscal_year_org_per_user_weeklyeffort(request.user.organizations)
        extra_context = {
            **(extra_context or {}),
            "summary": summary_results,
            "expected": expected_hours,
            "months": all_months,
            "monthly_exceed_percentage": int(settings.PROJECT_EFFORT_EXCEED_PERCENTAGE * 100),
        }
        return super().changelist_view(request, extra_context)


@admin.register(ProjectWeeklyEffortUnlock)
class ProjectWeeklyEffortUnlockAdmin(UserCreatedBaseModelAdmin):
    """週間稼働アンロックの管理・承認 (kippo#33 / T18).

    AllowIsStaffAdminMixin is deliberately NOT applied: staff could otherwise unlock their own
    closed weeks. Default Django model permissions apply (superusers, or staff explicitly granted).
    REST API 経由の申請はここで承認する。adminが直接作成したアンロックは作成時に自動承認される
    (ただし superuser を除き自分自身のためのものは保留のまま — 自分の週を勝手に開けられない原則)。
    """

    list_display = (
        "organization",
        "user",
        "week_start",
        "get_reason_short",
        "is_active",
        "approved_by",
        "approved_datetime",
        "expires_datetime",
        "created_by",
    )
    list_filter = ("organization", "approved_datetime")
    ordering = ("-week_start", "user")
    search_fields = ("user__username", "user__last_name", "reason")
    actions = ("approve_selected",)

    REASON_PREVIEW_CHARS = 40

    @admin.display(description=_("理由"))
    def get_reason_short(self, obj: ProjectWeeklyEffortUnlock) -> str:
        if not obj.reason:
            return "-"
        limit = self.REASON_PREVIEW_CHARS
        return f"{obj.reason[:limit]}…" if len(obj.reason) > limit else obj.reason

    @admin.display(boolean=True, description=_("有効"))
    def is_active(self, obj: ProjectWeeklyEffortUnlock) -> bool:
        return obj.is_active()

    def save_model(self, request: HttpRequest, obj: ProjectWeeklyEffortUnlock, form: Form, change: bool) -> None:
        creating = getattr(obj, "pk", None) is None
        super().save_model(request, obj, form, change)  # sets created_by/updated_by and saves
        # adminが申請フローを経ず直接作成したアンロックは即承認する (他ユーザ向けのみ; superuserは自分向けも可)
        if creating and not obj.is_approved and (request.user.is_superuser or obj.user_id != request.user.pk):
            obj.approve(approved_by=request.user, expires_datetime=obj.expires_datetime)

    @admin.action(description=_("選択したアンロック申請を承認する"))
    def approve_selected(self, request: HttpRequest, queryset: "QuerySet[ProjectWeeklyEffortUnlock]") -> None:
        approved = 0
        skipped_self = 0
        for unlock in queryset:
            if unlock.is_approved:
                continue
            if unlock.created_by_id == request.user.pk and not request.user.is_superuser:
                skipped_self += 1
                continue
            unlock.approve(approved_by=request.user, expires_datetime=unlock.expires_datetime)
            approved += 1
        if approved:
            self.message_user(request, f"{approved}件のアンロックを承認しました。", messages.SUCCESS)
        if skipped_self:
            self.message_user(request, f"{skipped_self}件は自分の申請のため承認をスキップしました。", messages.WARNING)
