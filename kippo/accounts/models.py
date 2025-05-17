import datetime
import logging
import secrets
import string
import uuid
from collections import Counter
from collections.abc import Generator

from commons.definitions import SATURDAY, SUNDAY
from commons.models import TimestampedModel, UserCreatedBaseModel
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, validate_email
from django.db import models
from django.db.models import QuerySet
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from accounts.definitions import AttendanceRecordCategory

logger = logging.getLogger(__name__)


JAPAN_FISCALYEAR_START_MONTH = 4


def generate_random_secret(n: int = 20) -> str:
    """Generate a random string of n length"""
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(n))


class KippoOrganization(UserCreatedBaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=256)
    github_organization_name = models.CharField(max_length=100, unique=True)
    day_workhours = models.PositiveSmallIntegerField(default=7, help_text=_("Defines the number of hours in the workday"))
    default_task_category = models.CharField(
        max_length=256,
        default=settings.DEFAULT_KIPPOTASK_CATEGORY,
        blank=True,
        help_text=_("Default category to apply to KippoTask objects"),
    )
    default_task_display_state = models.CharField(
        max_length=150, default="in-progress", help_text=_("Default Task STATE to show on initial task view")
    )
    default_columnset = models.ForeignKey(
        "projects.ProjectColumnSet",
        on_delete=models.DO_NOTHING,
        null=True,
        default=None,
        blank=True,
        help_text=_("If defined, this will be set as the default ColumnSet when a Project is created"),
    )
    default_labelset = models.ForeignKey(
        "octocat.GithubRepositoryLabelSet",
        on_delete=models.DO_NOTHING,
        null=True,
        default=None,
        blank=True,
        help_text=_("If defined newly identified GithubRepository will AUTOMATICALLY have this LabelSet assigned"),
    )
    google_forms_project_survey_url = models.URLField(default="", blank=True, help_text=_('If a "Project Survey" is defined, include here'))
    google_forms_project_survey_projectid_entryid = models.CharField(
        max_length=255,
        default="",
        blank=True,
        help_text=_('"Project Identifier" field in survey (ex: "entry:123456789")'),
    )
    github_webhook_secret = models.CharField(max_length=20, default=generate_random_secret, editable=False, help_text=_("Github Webhook Secret"))
    weekly_project_time_deadline = models.TimeField(
        default=datetime.time(12, 5), help_text=_("Cutoff deadline defining the latest time status will be included in the weekly report")
    )
    slack_api_token = models.CharField(
        max_length=60,
        blank=True,
        default="",
        help_text=_("REQUIRED if slack channel reporting is desired"),
    )
    slack_signing_secret = models.CharField(max_length=255, blank=True, default="", help_text=_("Slack signing secret for this organization"))
    slack_channel_name = models.CharField(
        max_length=60,
        blank=True,
        default="#kippo",
        help_text=_("REQUIRED if slack channel reporting is desired"),
    )
    slack_bot_name = models.CharField(
        max_length=60,
        blank=True,
        default="kippo",
        help_text=_("REQUIRED if slack channel reporting is desired"),
    )
    slack_bot_iconurl = models.URLField(blank=True, default="", help_text=_("URL link to slack bot display image"))
    slack_command_name = models.CharField(max_length=15, default="kippo", help_text=_("Slack command name to use"))
    slack_weekly_project_report_channel = models.CharField(
        max_length=50, blank=True, default="#kippo", help_text=_("Slack channel to post weekly project report")
    )
    slack_attendance_report_channel = models.CharField(
        max_length=50, blank=True, default="#kippo", help_text=_("Slack channel to post attendance report")
    )
    enable_slack_channel_reporting = models.BooleanField(
        default=False,
        help_text=_("Enable Slack channel reporting for this organization"),
    )

    fiscalyear_start_month = models.PositiveSmallIntegerField(
        default=JAPAN_FISCALYEAR_START_MONTH, validators=[MaxValueValidator(12), MinValueValidator(1)]
    )

    @property
    def email_domains(self) -> QuerySet:
        domains = EmailDomain.objects.filter(organization=self)
        return domains

    @property
    def slug(self):
        return slugify(self.name, allow_unicode=True)

    def get_membership_kippousers(self) -> list["KippoUser"]:
        memberships = OrganizationMembership.objects.filter(organization=self).order_by("user__username").select_related("user")
        users = [m.user for m in memberships]
        return users

    def get_github_developer_kippousers(self) -> list["KippoUser"]:
        """Get KippoUser objects for users with a github login, membership to this organization, and is_developer=True status"""
        developer_memberships = OrganizationMembership.objects.filter(
            user__github_login__isnull=False, organization=self, is_developer=True
        ).select_related("user")
        developer_users = [m.user for m in developer_memberships]
        return developer_users

    @property
    def github_webhook_url(self) -> str:
        return f"{settings.URL_PREFIX}/octocat/webhook/{self.pk}/"

    @property
    def slack_webhook_url(self) -> str:
        return f"{settings.URL_PREFIX}/accounts/slack/webhook/{self.pk}/"

    def create_unassigned_kippouser(self):
        # AUTO-CREATE organization specific unassigned user
        cli_manager_user = get_climanager_user()
        unassigned_username = f"{settings.UNASSIGNED_USER_GITHUB_LOGIN_PREFIX}-{self.slug}"
        unassigned_github_login = unassigned_username
        logger.info(f"Creating ({unassigned_github_login}) user for: {self.name}")
        user = KippoUser(username=unassigned_username, github_login=unassigned_github_login, is_staff=False, is_superuser=False)
        user.save()

        membership = OrganizationMembership(user=user, organization=self, is_developer=True, created_by=cli_manager_user, updated_by=cli_manager_user)
        membership.save()

    def get_unassigned_kippouser(self):
        membership = OrganizationMembership.objects.get(organization=self, user__username__startswith=settings.UNASSIGNED_USER_GITHUB_LOGIN_PREFIX)
        return membership.user

    def clean(self):
        if self.google_forms_project_survey_url and not self.google_forms_project_survey_url.endswith("viewform"):
            raise ValidationError(f'Google Forms URL does not to end with expected "viewform": {self.google_forms_project_survey_url}')

        if self.enable_slack_channel_reporting and not all(
            (self.slack_api_token, self.slack_bot_name, self.slack_channel_name, self.slack_signing_secret)
        ):
            raise ValidationError(
                "'slack_api_token', 'slack_bot_name' and 'slack_channel_name' must be defined if 'enable_slack_channel_reporting' is True"
            )

    def save(self, *args, **kwargs):
        if self._state.adding:  # created (for when using UUIDField as id)
            super().save(*args, **kwargs)
            self.create_unassigned_kippouser()
        else:
            super().save(*args, **kwargs)

    def get_next_fiscal_year(self) -> timezone.datetime:
        current = timezone.now()
        while current.month != self.fiscalyear_start_month:
            current += timezone.timedelta(days=1)
        next_fiscal_year = current.replace(day=1)
        return next_fiscal_year

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.name}-{self.github_organization_name})"


class EmailDomain(UserCreatedBaseModel):
    organization = models.ForeignKey(KippoOrganization, on_delete=models.CASCADE)
    domain = models.CharField(
        max_length=255,
        help_text=_("Organization email domains allowed to access organization information [USERNAME@{DOMAIN}]"),
    )
    is_staff_domain = models.BooleanField(default=True, help_text=_("Domain has access to admin"))

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.domain})"

    def clean(self):
        email_address_with_domain = f"test@{self.domain}"
        try:
            validate_email(email_address_with_domain)  # will raise ValidationError on failure
        except ValidationError:
            raise ValidationError(f'"{self.domain}" is not a valid EMAIL DOMAIN!') from None


class OrganizationMembership(UserCreatedBaseModel):
    user = models.ForeignKey("KippoUser", on_delete=models.DO_NOTHING)
    organization = models.ForeignKey("KippoOrganization", on_delete=models.DO_NOTHING)
    email = models.EmailField(blank=True, default="", help_text=_("Email address with Organization"))
    slack_username = models.CharField(max_length=100, blank=True, default="", help_text=_("Slack username"))
    slack_user_id = models.CharField(max_length=100, blank=True, default="", help_text=_("Slack user ID"))
    # TODO: add OPTIONAL -- contract_start, contract_end
    # in order to define the start/stop of when the user may work
    is_project_manager = models.BooleanField(default=False)
    is_developer = models.BooleanField(default=True)
    # TODO: Update to allow for fractional days 1.0 - 0.0
    sunday = models.BooleanField(default=False, help_text=_("Works Sunday"))
    monday = models.BooleanField(default=True, help_text=_("Works Monday"))
    tuesday = models.BooleanField(default=True, help_text=_("Works Tuesday"))
    wednesday = models.BooleanField(default=True, help_text=_("Works Wednesday"))
    thursday = models.BooleanField(default=True, help_text=_("Works Thursday"))
    friday = models.BooleanField(default=True, help_text=_("Works Friday"))
    saturday = models.BooleanField(default=False, help_text=_("Works Saturday"))

    class Meta:
        ordering = ["user__username"]
        unique_together = (
            "user",
            "organization",
        )

    @property
    def committed_days(self) -> int:
        weekdays = ("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday")
        result = sum(1 for day in weekdays if getattr(self, day))
        return result

    @property
    def committed_weekdays(self) -> list[int]:
        """Return the integer weekday values for committed days"""
        workday_attrs = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        weekdays = []
        for weekday, attr in enumerate(workday_attrs):  # 0 - start (monday)
            is_committed = getattr(self, attr)
            if is_committed:
                weekdays.append(weekday)
        return weekdays

    def get_workday_identifers(self) -> tuple[str]:
        """Convert membership workdays to string list used by qlu scheduler"""
        workday_attrs = ("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday")
        identifiers = []
        for attr in workday_attrs:
            if getattr(self, attr):
                workday_id = attr.capitalize()[:3]  # 'sunday' -> 'Sun'
                identifiers.append(workday_id)
        return tuple(identifiers)

    @property
    def email_domain(self):
        domain = self.email.split("@")[-1]  # NAME@DOMAIN.COM -> [ 'NAME', 'DOMAIN.COM']
        return domain

    def clean(self, *args, **kwargs):
        super().clean(*args, **kwargs)

        # check that given email matches expected organization email domain
        organization_domains = [d.domain for d in self.organization.email_domains]
        if self.email and self.email_domain not in organization_domains:
            raise ValidationError(f"Invalid email address ({self.email}) for organization({self.organization}) domains: {organization_domains}")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        # update user with is_staff/is_active state based on the organization domain.is_staff_domain value
        logger.info(f"User({self.user}) added to {self.organization}!")

        is_staff = False
        for domain in self.organization.email_domains:
            if domain.is_staff_domain:
                is_staff = True
                break

        if is_staff:
            logger.info(f"Updating User({self.user.username}) is_staff/is_active -> True")
            self.user.is_staff = True
            self.user.is_active = True
            self.user.save()

    def __str__(self) -> str:
        return f"OrganizationMembership({self.organization}:{self.user.username})"


class KippoUser(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    memberships = models.ManyToManyField(
        KippoOrganization,
        through="OrganizationMembership",
        through_fields=("user", "organization"),
        blank=True,
        default=None,
    )
    github_login = models.CharField(max_length=100, blank=True, default="", help_text="Github Login username")
    is_github_outside_collaborator = models.BooleanField(default=False, help_text=_("Set to True if User is an outside collaborator"))
    holiday_country = models.ForeignKey(
        "accounts.Country",
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        help_text=_("Country that user participates in holidays"),
    )

    @property
    def display_name(self):
        github_login_display = ""
        if self.github_login and self.github_login.startswith("unassigned"):
            github_login_display = " (unassigned)"
        elif self.github_login:
            github_login_display = f" ({self.github_login})"

        return f" {self.first_name} {self.last_name}{github_login_display}"

    def personal_holiday_dates(self) -> Generator[datetime.date]:
        for holiday in PersonalHoliday.objects.filter(user=self):
            holiday_start_date = holiday.day
            for days in range(holiday.duration):
                date = holiday_start_date + timezone.timedelta(days=days)
                yield date

    def public_holiday_dates(self) -> list:
        return PublicHoliday.objects.filter(country=self.holiday_country).values_list("day", flat=True)

    @property
    def organizations(self) -> QuerySet:
        organization_ids = OrganizationMembership.objects.filter(user=self).values_list("organization", flat=True).distinct()
        return KippoOrganization.objects.filter(id__in=organization_ids)

    def get_membership(self, organization: KippoOrganization) -> OrganizationMembership:
        return OrganizationMembership.objects.get(user=self, organization=organization)

    def get_assigned_kippotasks(self) -> QuerySet:
        from tasks.models import KippoTask

        return KippoTask.objects.filter(is_closed=False, assignee=self)

    def get_estimatedays(self) -> float:
        tasks = self.get_assigned_kippotasks()
        total_estimatedays = 0
        for task in tasks:
            active_columnnames = task.project.get_active_column_names()
            lastest_taskstatus = task.latest_kippotaskstatus()
            if lastest_taskstatus.state in active_columnnames:
                total_estimatedays += lastest_taskstatus.estimate_days if lastest_taskstatus.estimate_days else 0
        return float(total_estimatedays)

    def __str__(self) -> str:
        display_name = f"{self.username}"
        if self.last_name and self.first_name:
            display_name = f"({self.last_name.capitalize()}, {self.first_name.capitalize()}) {self.username}"
        return display_name


class OrganizationInvite(UserCreatedBaseModel):
    organization = models.ForeignKey(KippoOrganization, on_delete=models.CASCADE)
    email = models.EmailField(db_index=True, help_text=_("Email address to send invite to"))
    expiration_date = models.DateField(editable=False, help_text=_("Date the invite expires"))
    is_complete = models.BooleanField(default=False, editable=False, help_text=_("True if the invite has been processed"))
    processed_datetime = models.DateTimeField(null=True, blank=True, editable=False, help_text=_("Date the invite was processed"))

    def __str__(self) -> str:
        return f"OrganizationInvite({self.organization.name} -> {self.email})"

    def create_organizationmembership(self, user: KippoUser):
        system_user = get_climanager_user()

        logger.info(f"Creating OrganizationMembership for {user.username} ({self.organization}) ...")
        membership = OrganizationMembership(
            user=user,
            organization=self.organization,
            email=self.email,
            is_project_manager=False,
            is_developer=False,
            created_by=system_user,
            updated_by=system_user,
        )
        membership.save()
        logger.info(f"Creating OrganizationMembership for {user.username} ({self.organization}) ... DONE")
        self.is_complete = True
        self.processed_datetime = timezone.now()
        self.save()

        for domain in self.organization.email_domains:
            if self.email.endswith(domain.domain) and domain.is_staff_domain:
                logger.info(f"Updating User({user.username}) is_staff -> True ...")
                user.is_staff = True
                user.save()
                logger.info(f"Updating User({user.username}) is_staff -> True ... DONE")
                break

        return membership

    def save(self, *args, **kwargs):
        if not self.expiration_date:
            # set expiration date to 7 days from now
            self.expiration_date = (timezone.now() + timezone.timedelta(days=settings.ORGANIZATIONINVITE_EXPIRATION_DAYS)).date()
        super().save(*args, **kwargs)


class PersonalHoliday(models.Model):
    user = models.ForeignKey(KippoUser, on_delete=models.CASCADE, editable=True)
    created_datetime = models.DateTimeField(editable=False, auto_now_add=True)
    is_half = models.BooleanField(default=False, help_text=_("Select if taking only a half day"))
    day = models.DateField()
    duration = models.SmallIntegerField(default=1, help_text=_("How many days (including weekends/existing holidays)"))

    class Meta:
        verbose_name = _("個人休日")
        verbose_name_plural = verbose_name
        ordering = ["-day"]

    def __str__(self) -> str:
        return f"PersonalHoliday({self.user.username} [{self.day} ({self.duration})])"

    def get_weeklyeffort_hours(self, today: datetime.date | None = None) -> Generator:
        # "project": effort.project.name,
        # "week_start": effort.week_start.strftime("%Y%m%d"),
        # "user": effort.user.display_name,
        # "hours": effort.hours,
        if not today:
            today = timezone.now().date()
        public_holidays = PublicHoliday.objects.filter(day__gte=today, country=self.user.holiday_country).values_list("day", flat=True)
        c = Counter()
        for day_count in range(self.duration):
            target_day = self.day + timezone.timedelta(days=day_count)
            week_start = target_day - timezone.timedelta(days=target_day.weekday())
            if target_day.weekday() not in (SATURDAY, SUNDAY) and target_day not in public_holidays:
                if self.is_half:
                    hours = 4
                else:
                    hours = 8
                c[week_start.strftime("%Y%m%d")] += hours
        return (
            {
                "project": "PersonalHoliday",
                "week_start": week_start,
                "user": self.user.display_name,
                "hours": c[week_start],
            }
            for week_start in c
        )


class Country(models.Model):
    name = models.CharField(max_length=130, help_text=_("Name of Country"))
    alpha_2 = models.CharField(max_length=2, help_text=_("ISO-3166 2 letter abbreviation"))
    alpha_3 = models.CharField(max_length=3, help_text=_("ISO-3166 3 letter abbreviation"))
    country_code = models.CharField(max_length=3, help_text=_("ISO-3166 3 digit country-code"))
    region = models.CharField(max_length=50, help_text=_("Global Region"))

    def __str__(self) -> str:
        return f"({self.alpha_3}) {self.name} "


class PublicHoliday(models.Model):
    country = models.ForeignKey(Country, on_delete=models.CASCADE)
    name = models.CharField(max_length=150, help_text=_("Holiday Name"))
    day = models.DateField()

    class Meta:
        ordering = ["-day"]

    def __str__(self) -> str:
        return f"{self.name} {self.day} ({self.country.alpha_3})"


def get_climanager_user() -> KippoUser:
    user = KippoUser.objects.get(username="cli-manager")
    return user


@receiver(pre_delete, sender=KippoUser)
def delete_kippouser_organizationmemberships(sender: type[KippoUser], instance: KippoUser, **_) -> None:  # noqa: ARG001
    membership_count = OrganizationMembership.objects.filter(user=instance).count()
    logger.info(f"Deleting ({membership_count}) OrganizationMembership(s) for User: {instance.username}")
    OrganizationMembership.objects.filter(user=instance).delete()


class SlackCommand(TimestampedModel):
    organization = models.ForeignKey(KippoOrganization, null=True, on_delete=models.CASCADE, help_text=_("Organization that created the command"))
    user = models.ForeignKey(KippoUser, null=True, on_delete=models.CASCADE, help_text=_("User that created the command"))
    is_valid = models.BooleanField(default=False, help_text=_("True if the command is valid"))
    sub_command = models.CharField(max_length=255, blank=True, default="", help_text=_("Command that was sent"))
    text = models.CharField(max_length=255, blank=True, default="", help_text=_("Text that was sent"))
    response_url = models.URLField(max_length=255, blank=True, default="", help_text=_("Response URL that was sent"))
    payload = models.JSONField(blank=True, default=dict, help_text=_("Payload that was sent"))
    processed_datetime = models.DateTimeField(null=True, blank=True, help_text=_("Date the command was processed"))


class AttendanceRecord(TimestampedModel):
    user = models.ForeignKey(KippoUser, on_delete=models.CASCADE, help_text=_("User that created the command"))
    organization = models.ForeignKey(KippoOrganization, on_delete=models.CASCADE, help_text=_("Organization that created the command"))
    date = models.DateField(default=timezone.localdate, help_text=_("Date of the attendance record"))
    category = models.CharField(
        max_length=255,
        blank=True,
        default=AttendanceRecordCategory.START,
        choices=AttendanceRecordCategory.choices(),
        help_text=_("Category of the attendance record"),
    )
    entry_datetime = models.DateTimeField(auto_now_add=True)
    source_command = models.ForeignKey(
        SlackCommand, on_delete=models.CASCADE, null=True, blank=True, help_text=_("Slack command that created the attendance record")
    )
