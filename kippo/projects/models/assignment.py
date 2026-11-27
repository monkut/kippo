"""Project participants, monthly assignments and per-role daily rates."""

import logging

from accounts.models import OrganizationMembership
from commons.functions import add_months
from commons.models import UserCreatedBaseModel
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..definitions import (
    ProjectRoles,
)
from .constants import MAX_ASSIGNMENT_PERCENTAGE, PARTICIPANT_MAX_START_MONTHS_AHEAD, UNASSIGNED_USER_GITHUB_LOGIN_PREFIX
from .project import KippoProject

logger = logging.getLogger(__name__)


class ProjectParticipant(UserCreatedBaseModel):
    """プロジェクト参加者 (kiconiaworks/kippo#75): the parent of a user's monthly assignments on a project.

    `percentage` and `role` are the defaults for the participant's monthly rows. Creating rows for the
    remaining period (R1) and applying edited defaults (E2) live in `projects.services.participants`;
    `delete()` removes the remaining months (X1).
    """

    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey("accounts.KippoUser", on_delete=models.DO_NOTHING, related_name="projectparticipant_user")
    percentage = models.SmallIntegerField(
        validators=(MinValueValidator(0),),
        help_text=_("Default monthly workload percentage for this participant"),
    )
    role = models.CharField(
        max_length=50,
        choices=ProjectRoles.choices(),
        blank=True,
        default="",
        help_text=_("Default billing-rate role for this participant's monthly assignments"),
    )

    class Meta:
        verbose_name = _("プロジェクト参加者")
        verbose_name_plural = verbose_name
        unique_together = ("project", "user")

    def __str__(self) -> str:
        return f"{self.project} - {self.user}"

    def clean(self) -> None:
        super().clean()
        if not (self.project_id and self.user_id):
            return
        is_member = OrganizationMembership.objects.filter(user_id=self.user_id, organization_id=self.project.organization_id).exists()
        if not is_member:
            raise ValidationError(
                {"user": _("User is not a member of the project's organization '%(org)s'") % {"org": self.project.organization}},
                code="invalid_user_organization",
            )
        if self.user.username.startswith(UNASSIGNED_USER_GITHUB_LOGIN_PREFIX):
            raise ValidationError(
                {"user": ValidationError(_("The organization's (unassigned) user cannot be a participant"), code="invalid_user_unassigned")}
            )
        if self._state.adding:
            # R1 needs the project period (matches the API's 400 on missing dates, kippo#72 D6).
            if self.project.start_date is None or self.project.target_date is None:
                raise ValidationError(
                    _("Set the project's start date and target date before adding participants"),
                    code="project_period_required",
                )
            period_error = participant_period_error(self.project)
            if period_error:
                _field, message, code = period_error
                raise ValidationError(message, code=code)
            return
        previous_user_id = ProjectParticipant.objects.filter(pk=self.pk).values_list("user_id", flat=True).first()
        if previous_user_id is not None and previous_user_id != self.user_id:
            raise ValidationError(
                {"user": _("A participant's user cannot be changed; delete the participant and add the new user")},
                code="participant_user_immutable",
            )

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # noqa: ANN201
        """X1: delete this participant's rows from the current month (JST) on; past rows are kept (participant → null)."""
        self.monthly_assignments.filter(month__gte=timezone.localdate().replace(day=1)).delete()
        return super().delete(*args, **kwargs)


def participant_period_error(project: KippoProject) -> tuple[str, str, str] | None:
    """Why participants can't be added to `project` today (JST), as (field, message, code); None when allowed (kippo#75).

    Checked only when adding a participant; both dates must already be set.
    - The project period has ended: target_date is before today.
    - The project starts too far ahead: start_date is more than PARTICIPANT_MAX_START_MONTHS_AHEAD months after today.
    """
    today = timezone.localdate()
    if project.target_date < today:
        return (
            "target_date",
            _("プロジェクト期間が終了しているため参加者を追加できません（完了予定日: %(target)s）。完了予定日を更新してから追加してください。")
            % {"target": project.target_date.isoformat()},
            "project_period_ended",
        )
    latest_start = add_months(today, PARTICIPANT_MAX_START_MONTHS_AHEAD)
    if project.start_date > latest_start:
        return (
            "start_date",
            _("開始日（%(start)s）が%(months)dか月以上先のため参加者を追加できません。%(latest)s 以降に追加してください。")
            % {
                "start": project.start_date.isoformat(),
                "months": PARTICIPANT_MAX_START_MONTHS_AHEAD,
                "latest": add_months(project.start_date, -PARTICIPANT_MAX_START_MONTHS_AHEAD).isoformat(),
            },
            "project_start_too_far_ahead",
        )
    return None


class ProjectMonthlyAssignment(UserCreatedBaseModel):
    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE, related_name="projectassignment_project")
    user = models.ForeignKey("accounts.KippoUser", on_delete=models.DO_NOTHING, related_name="projectassignment_user")
    # Set from (project, user) in clean(); null only for past rows kept after their participant was deleted (kippo#75 X1).
    participant = models.ForeignKey(
        ProjectParticipant,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="monthly_assignments",
    )
    month = models.DateField(null=True, blank=True, help_text=_("Assignment month (defaults to project start_date month)"))
    is_confirmed = models.BooleanField(default=False, help_text=_("Assignment is confirmed or not"))
    percentage = models.SmallIntegerField(
        help_text=_("Workload percentage assigned to project from available workload available for project organization")
    )
    role = models.CharField(
        max_length=50,
        choices=ProjectRoles.choices(),
        blank=True,
        default="",
        help_text=_(
            "Billing-rate role for effort-based billing; selects the project's ProjectAssignmentRate "
            "for this user's logged effort. Empty falls back to the developer rate."
        ),
    )

    class Meta:
        unique_together = ("project", "user", "month")
        indexes = [models.Index(fields=["month"])]

    def clean(self) -> None:
        super().clean()
        # Default month to the project's start_date month (first day of month)
        if not self.month and self.project and self.project.start_date:
            self.month = self.project.start_date.replace(day=1)

        # Validate that user is a member of the project's organization
        if self.user and self.project:
            is_member = OrganizationMembership.objects.filter(user=self.user, organization=self.project.organization).exists()
            if not is_member:
                raise ValidationError(
                    _("User '%(user)s' is not a member of the project's organization '%(org)s'"),
                    params={"user": self.user, "org": self.project.organization},
                    code="invalid_user_organization",
                )

        self._link_participant()

    def _link_participant(self) -> None:
        """O2 (kippo#75): a new row needs a ProjectParticipant for (project, user); link it.

        An existing row whose participant was deleted (past month kept by X1) may still be edited unlinked.
        """
        if not (self.project_id and self.user_id):
            return
        participant = ProjectParticipant.objects.filter(project_id=self.project_id, user_id=self.user_id).first()
        if participant is not None:
            self.participant = participant
            return
        if self._state.adding or self.participant_id is not None:
            raise ValidationError(
                _("User '%(user)s' is not a participant of project '%(project)s'; add them as a project participant first"),
                params={"user": self.user, "project": self.project},
                code="participant_required",
            )

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)
        # Warn (do not block) when the user's total percentage for the organization in this
        # month exceeds 100%. Over-allocation is permitted; UI / admin surfaces are
        # responsible for visually displaying the warning to end users.
        if self.user and self.project and self.month:
            total_percentage = (
                ProjectMonthlyAssignment.objects.filter(
                    user=self.user,
                    project__organization=self.project.organization,
                    month=self.month,
                ).aggregate(total=Sum("percentage"))["total"]
                or 0
            )
            if total_percentage > MAX_ASSIGNMENT_PERCENTAGE:
                logger.warning(
                    "User '%s' has total assignment of %d%% for organization '%s' in month %s (exceeds 100%%)",
                    self.user,
                    total_percentage,
                    self.project.organization,
                    self.month.strftime("%Y-%m"),
                )


class ProjectAssignmentRate(UserCreatedBaseModel):
    """Daily rate configuration per role for a project."""

    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE, related_name="assignment_rates")
    role = models.CharField(max_length=50, choices=ProjectRoles.choices())
    rate_per_day = models.PositiveIntegerField(default=settings.DEFAULT_PROJECT_DAILY_RATE)

    class Meta:
        verbose_name = _("Project Assignment Rate")
        verbose_name_plural = _("Project Assignment Rates")
        unique_together = ("project", "role")

    def __str__(self) -> str:
        return f"{self.project.display_label} - {self.role}: {self.rate_per_day}"
