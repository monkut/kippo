# Generated by Django 2.2.4 on 2019-08-07 01:33

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0006_kippoproject_column_info'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectcolumnset',
            name='default_column_name',
            field=models.CharField(default='planning', max_length=256, verbose_name='Task default column name (Used when project column position is not known)'),
        ),
    ]
