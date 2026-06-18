"""週間稼働の締め前リマインダ (kippo#33 / #3).

締め (月単位) の 24時間前に組織の Slack チャンネル (slack_channel_name, 既定 #kippo) へ通知し、
締め対象月に未入力の週がある開発メンバを @メンションする。日次のスケジュールジョブから呼ばれる
(`projects.handlers.functions.run_weeklyeffort_close_reminder`)。各締めは前方24時間ウィンドウに
ちょうど1回だけ入るため、締めごとに1回だけ通知される。
"""

import datetime
import logging

from accounts.models import KippoOrganization, OrganizationMembership
from commons.functions import first_of_month, last_of_month
from django.conf import settings
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ..models import ProjectWeeklyEffort

logger = logging.getLogger(__name__)

DEFAULT_REMINDER_WINDOW = datetime.timedelta(hours=24)
# 締め対象月の候補数: 締めは対象月の翌月初旬に行われるため、now 近辺の数か月を遡って確認する。
_CANDIDATE_MONTH_LOOKBACK = 3


def mondays_in_month(month_first: datetime.date) -> list[datetime.date]:
    """その月に属する週開始 (月曜) の一覧。"""
    last = last_of_month(month_first)
    current = month_first
    while current.weekday() != 0:  # Monday == 0
        current += datetime.timedelta(days=1)
    weeks = []
    while current <= last:
        weeks.append(current)
        current += datetime.timedelta(days=7)
    return weeks


class WeeklyEffortCloseReminderManager:
    """組織ごとの締め前リマインダの算出・送信。"""

    def __init__(self, organization: KippoOrganization, now: datetime.datetime | None = None) -> None:
        self.organization = organization
        self.now = now  # None のときは get_upcoming_close で timezone.now() を使う
        self.client = WebClient(token=organization.slack_api_token)

    def _current_now(self) -> datetime.datetime:
        from django.utils import timezone

        return self.now or timezone.now()

    def get_upcoming_close(self, within: datetime.timedelta = DEFAULT_REMINDER_WINDOW) -> tuple[datetime.datetime, datetime.date] | None:
        """締め日時が (now, now+within] に入る締め対象月があれば (締め日時, 対象月初日) を返す。"""
        now = self._current_now()
        month_first = first_of_month(now.date())
        for _ in range(_CANDIDATE_MONTH_LOOKBACK):
            close_datetime = self.organization.get_weeklyeffort_close_datetime(month_first)
            if now < close_datetime <= now + within:
                return close_datetime, month_first
            month_first = first_of_month(month_first - datetime.timedelta(days=1))  # 前月の初日
        return None

    def get_missing_by_member(self, closing_month_first: datetime.date) -> list[tuple[OrganizationMembership, list[datetime.date]]]:
        """締め対象月の各週で稼働未入力の開発メンバと、その未入力週の一覧。"""
        week_starts = mondays_in_month(closing_month_first)
        logged = set(
            ProjectWeeklyEffort.objects.filter(project__organization=self.organization, week_start__in=week_starts).values_list(
                "user_id", "week_start"
            )
        )
        memberships = OrganizationMembership.objects.filter(organization=self.organization, is_developer=True).select_related("user")
        results = []
        for membership in memberships:
            missing = [week_start for week_start in week_starts if (membership.user_id, week_start) not in logged]
            if missing:
                results.append((membership, missing))
        return results

    @staticmethod
    def _mention(membership: OrganizationMembership) -> str:
        if membership.slack_user_id:
            return f"<@{membership.slack_user_id}>"
        return membership.user.display_name

    def build_blocks(
        self,
        close_datetime: datetime.datetime,
        closing_month_first: datetime.date,
        missing_by_member: list[tuple[OrganizationMembership, list[datetime.date]]],
    ) -> list[dict]:
        close_jst = close_datetime.astimezone(settings.JST)
        header = (
            f":alarm_clock: *{closing_month_first.strftime('%Y年%-m月')}* の週間稼働の締めが "
            f"*{close_jst.strftime('%-m月%-d日 %H:%M')} (JST)* に行われます。未入力の方は入力をお願いします。"
        )
        lines = []
        for membership, missing in missing_by_member:
            weeks = "、".join(week_start.strftime("%-m月%-d日週") for week_start in missing)
            lines.append(f"• {self._mention(membership)}: {weeks}")
        return [
            {"type": "section", "text": {"type": "mrkdwn", "text": header}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        ]

    def post(self, within: datetime.timedelta = DEFAULT_REMINDER_WINDOW) -> list[dict] | None:
        """締めが24時間以内かつ未入力メンバが居る場合に通知を送る。送ったブロックを返す (送らなければ None)。"""
        if not self.organization.slack_api_token or not self.organization.slack_channel_name:
            logger.info(f"skipping close reminder for ({self.organization.name}): slack_api_token/slack_channel_name not configured")
            return None
        upcoming = self.get_upcoming_close(within)
        if upcoming is None:
            return None
        close_datetime, closing_month_first = upcoming
        missing_by_member = self.get_missing_by_member(closing_month_first)
        if not missing_by_member:
            logger.info(f"({self.organization.name}) close at {close_datetime.isoformat()} within window but no missing entries — no reminder")
            return None
        blocks = self.build_blocks(close_datetime, closing_month_first, missing_by_member)
        try:
            self.client.chat_postMessage(
                channel=self.organization.slack_channel_name,
                blocks=blocks,
                text=f"{closing_month_first.strftime('%Y年%-m月')}の週間稼働の締めが近づいています",
            )
        except SlackApiError:
            logger.exception(f"failed to post weekly-effort close reminder for ({self.organization.name})")
            return None
        return blocks
