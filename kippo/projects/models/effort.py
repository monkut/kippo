"""Weekly effort entries and their unlock requests."""

import datetime

from accounts.models import KippoOrganization, KippoUser
from commons.models import UserCreatedBaseModel
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..functions import previous_week_startdate
from .project import KippoProject


class ProjectWeeklyEffort(UserCreatedBaseModel):
    week_start = models.DateField(default=previous_week_startdate, help_text="Effort Week Start (MONDAY)")
    project = models.ForeignKey(KippoProject, on_delete=models.CASCADE, related_name="projectweeklyeffort_project")
    user = models.ForeignKey("accounts.KippoUser", on_delete=models.DO_NOTHING, related_name="projectweeklyeffort_user")
    hours = models.SmallIntegerField(
        validators=(MinValueValidator(0), MaxValueValidator(7 * 24)),
        help_text=_("Actual effort in hours performed on the project for the given 'week start' (0-168)"),
    )

    class Meta:
        verbose_name = _("プロジェクト週間稼働量")
        verbose_name_plural = verbose_name
        unique_together = ("week_start", "project", "user")

    @property
    def close_datetime(self) -> datetime.datetime:
        return self.project.organization.get_weeklyeffort_close_datetime(self.week_start)

    def is_closed(self, now: datetime.datetime | None = None) -> bool:
        return self.project.organization.is_weeklyeffort_closed(self.user, self.week_start, now=now)


class ProjectWeeklyEffortUnlock(UserCreatedBaseModel):
    """週間稼働アンロック: 特定の週/ユーザの締め後編集を期限付きで許可する (T18).

    フロー (kippo#33): ユーザが reason を添えて申請 (created_by=申請者, approved_datetime=None の保留状態) し、
    組織adminが承認すると approved_by/approved_datetime と expires_datetime (再ロック期限) が設定され、
    その時点から expires_datetime までの間だけ編集可能になる。expires_datetime を過ぎると自動的に再ロックされる
    (has_active_unlock が読み取り時に判定するためスケジュールタスクは不要)。
    """

    organization = models.ForeignKey("accounts.KippoOrganization", on_delete=models.CASCADE, related_name="projectweeklyeffortunlock_organization")
    user = models.ForeignKey("accounts.KippoUser", on_delete=models.CASCADE, related_name="projectweeklyeffortunlock_user")
    week_start = models.DateField(help_text=_("Unlocked Effort Week Start (MONDAY)"))
    reason = models.TextField(help_text=_("アンロック申請理由 (DCAA: 締め後編集には記録された正当な理由が必要)"))
    approved_by = models.ForeignKey(
        "accounts.KippoUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        default=None,
        related_name="projectweeklyeffortunlock_approved_by",
        help_text=_("承認した組織admin (未設定 = 承認待ち)"),
    )
    approved_datetime = models.DateTimeField(null=True, blank=True, default=None, help_text=_("承認日時 (未設定 = 承認待ち)"))
    expires_datetime = models.DateTimeField(
        null=True, blank=True, default=None, help_text=_("再ロック期限: 承認時に設定され、この日時を過ぎると編集不可に戻る")
    )

    class Meta:
        verbose_name = _("プロジェクト週間稼働アンロック")
        verbose_name_plural = verbose_name
        unique_together = ("organization", "user", "week_start")

    @property
    def is_approved(self) -> bool:
        return self.approved_datetime is not None

    def is_active(self, now: datetime.datetime | None = None) -> bool:
        """承認済みで、再ロック期限 (expires_datetime) 前であれば有効。"""
        if not self.is_approved or self.expires_datetime is None:
            return False
        now = now or timezone.now()
        return self.expires_datetime > now

    def approve(self, approved_by: "KippoUser", expires_datetime: datetime.datetime | None = None, now: datetime.datetime | None = None) -> None:
        """組織adminによる承認: 承認者・承認日時を記録し、再ロック期限を設定する (#1/#2)。"""
        now = now or timezone.now()
        self.approved_by = approved_by
        self.approved_datetime = now
        self.expires_datetime = expires_datetime or (now + datetime.timedelta(days=settings.DEFAULT_WEEKLY_EFFORT_UNLOCK_DAYS))
        self.save()

    @classmethod
    def _active_unlock_filter(cls, now: datetime.datetime | None = None) -> "models.QuerySet[ProjectWeeklyEffortUnlock]":
        """承認済み (approved_datetime) かつ再ロック期限前 (expires_datetime) のアンロックの単一定義 (kippo#33 / #5).

        単件判定 (has_active_unlock) と一覧用バッチ取得 (active_unlock_keys) が同じ述語を共有し、ロジックの二重化を防ぐ。
        """
        now = now or timezone.now()
        return cls.objects.filter(approved_datetime__isnull=False, expires_datetime__gt=now)

    @classmethod
    def has_active_unlock(
        cls, organization: KippoOrganization, user: KippoUser, week_start: datetime.date, now: datetime.datetime | None = None
    ) -> bool:
        return cls._active_unlock_filter(now).filter(organization=organization, user=user, week_start=week_start).exists()

    @classmethod
    def active_unlock_keys(cls, now: datetime.datetime | None = None) -> set[tuple]:
        """現在有効なアンロックの (organization_id, user_id, week_start) キー集合を1クエリで返す。

        一覧シリアライザで締め判定を行号ごとの EXISTS ではなくバッチ化するために使う (kippo#33 / #5)。
        """
        return set(cls._active_unlock_filter(now).values_list("organization_id", "user_id", "week_start"))

    def __str__(self) -> str:
        status = f"expires={self.expires_datetime.isoformat()}" if self.is_active() else ("pending" if not self.is_approved else "expired")
        return f"{self.user.username} {self.week_start} ({status})"
