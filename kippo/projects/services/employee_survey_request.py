"""振り返り従業員アンケートの依頼通知。

KippoProjectAdmin の action (`request_employee_survey_action`) から呼ばれ、組織の Slack チャンネル
(slack_channel_name, 既定 #kippo) へ投稿する。対象はそのプロジェクトに週間稼働 (ProjectWeeklyEffort)
の登録があり、かつ振り返り従業員アンケート (KippoProjectUserStatisfactionResult) が未回答のメンバで、
@メンションとプロジェクトを選択済みにした入力画面への直リンクを添える。
"""

import logging

from accounts.models import KippoOrganization, KippoUser, OrganizationMembership
from django.conf import settings
from django.urls import reverse
from slack_sdk import WebClient

from ..models import KippoProject, KippoProjectUserStatisfactionResult

logger = logging.getLogger(__name__)


class ProjectEmployeeSurveyRequestManager:
    """プロジェクト単位の振り返り従業員アンケート依頼の算出・送信。"""

    def __init__(self, organization: KippoOrganization) -> None:
        if not organization.slack_channel_name:
            raise ValueError("organization.slack_channel_name is not set; cannot post the survey request.")
        if not organization.slack_api_token:
            raise ValueError("organization.slack_api_token is not set; cannot post the survey request.")
        self.organization = organization
        self.client = WebClient(token=organization.slack_api_token)

    @staticmethod
    def get_pending_users(project: KippoProject) -> list[KippoUser]:
        """アンケート対象ユーザ: プロジェクトに週間稼働の登録があり、かつ未回答のメンバ。

        回答済みは unique_together ("project", "created_by") のとおり再回答できないため除外する
        (action を再実行しても未回答者だけに督促が飛ぶ)。組織の (unassigned) 番人ユーザも対象外。
        """
        responded_user_ids = KippoProjectUserStatisfactionResult.objects.filter(project=project).values("created_by_id")
        return list(
            KippoUser.objects.filter(projectweeklyeffort_user__project=project)
            .exclude(id__in=responded_user_ids)
            .exclude(username__startswith=settings.UNASSIGNED_USER_GITHUB_LOGIN_PREFIX)
            .order_by("username")
            .distinct()
        )

    def _mentions(self, users: list[KippoUser]) -> list[str]:
        """@メンション文字列。slack_user_id 未設定のメンバは display_name にフォールバックする。"""
        slack_user_ids = dict(
            OrganizationMembership.objects.filter(organization=self.organization, user__in=users)
            .exclude(slack_user_id="")
            .values_list("user_id", "slack_user_id")
        )
        return [
            f"<@{slack_user_ids[user.id]}>" if user.id in slack_user_ids else user.display_name.strip()  # display_name has a leading space
            for user in users
        ]

    @staticmethod
    def survey_url(project: KippoProject) -> str:
        """プロジェクトを選択済みにしたアンケート入力画面への絶対URL。

        Slack はリンクを解決できないため HOST_URL (デプロイ先オリジン) + URL_PREFIX を付ける
        (admin は URL_PREFIX の外にマウントされているので reverse() の戻り値には含まれない)。
        ?project=<id> は admin の get_changeform_initial_data が拾い、プロジェクトが選択された状態で
        フォームが開く。
        """
        path = reverse("admin:projects_kippoprojectuserstatisfactionresult_add")
        return f"{settings.HOST_URL}{settings.URL_PREFIX}{path}?project={project.id}"

    def build_blocks(self, project: KippoProject, users: list[KippoUser]) -> list[dict]:
        header = f":clipboard: *{project.name}* の振り返り従業員アンケートのご記入をお願いします。"
        return [
            {"type": "section", "text": {"type": "mrkdwn", "text": header}},
            {"type": "section", "text": {"type": "mrkdwn", "text": " ".join(self._mentions(users))}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f":memo: <{self.survey_url(project)}|アンケートの入力はこちら>"}},
        ]

    def post(self, project: KippoProject) -> list[KippoUser]:
        """依頼を投稿し、メンションしたユーザを返す。対象が居なければ投稿せず空リストを返す。

        SlackApiError は呼び出し側 (admin action) が個別に報告するため、ここでは捕捉しない。
        """
        users = self.get_pending_users(project)
        if not users:
            logger.info(f"({project.name}) no pending employee-survey users — nothing posted")
            return []
        self.client.chat_postMessage(
            channel=self.organization.slack_channel_name,
            blocks=self.build_blocks(project, users),
            text=f"{project.name} の振り返り従業員アンケートのご記入をお願いします",
        )
        return users
