# Generated by Django 2.1.1 on 2018-09-27 14:31

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('octocat', '0001_initial'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='githubrepository',
            unique_together={('name', 'api_url', 'html_url')},
        ),
    ]
