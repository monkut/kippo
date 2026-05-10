from django.db import migrations


def normalize_slack_channel_names(apps, schema_editor):
    KippoProject = apps.get_model("projects", "KippoProject")
    for project in KippoProject.objects.exclude(slack_channel_name="").iterator():
        normalized = project.slack_channel_name.strip().lstrip("#")
        if normalized != project.slack_channel_name:
            project.slack_channel_name = normalized
            project.save(update_fields=["slack_channel_name"])


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0028_rename_github_project_api_url_to_nodeid"),
    ]

    operations = [
        migrations.RunPython(normalize_slack_channel_names, migrations.RunPython.noop),
    ]
