import accounts.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0020_kippoorganization_listener_settings'),
    ]

    operations = [
        migrations.AddField(
            model_name='kippouser',
            name='timezone_name',
            field=models.CharField(blank=True, default='', help_text="IANA timezone the user works in (e.g. Asia/Tokyo). Leave blank to use the holiday country's timezone (when that country has exactly one), otherwise the organization's.", max_length=64, validators=[accounts.models.validate_timezone], verbose_name='タイムゾーン'),
        ),
    ]
