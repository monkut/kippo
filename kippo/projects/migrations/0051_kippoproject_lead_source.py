# Add KippoProject.lead_source (リード) — an optional static-choice CharField recording how the
# project/opportunity was sourced. Choices are snapshotted from the kiconiaworks organization's
# active project categories (see projects.definitions.VALID_LEAD_SOURCES).
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0050_saleskippoproject"),
    ]

    operations = [
        migrations.AddField(
            model_name="kippoproject",
            name="lead_source",
            field=models.CharField(
                blank=True,
                choices=[
                    ("ai-development", "AI開発"),
                    ("non-project", "非案件"),
                    ("sunx", "SUNX経由"),
                    ("info", "info"),
                    ("employee-referral", "社員紹介"),
                    ("customer-referral", "顧客紹介"),
                    ("continuation", "継続"),
                ],
                default="",
                help_text="案件のリード獲得元（任意）",
                max_length=32,
                verbose_name="リード",
            ),
        ),
    ]
