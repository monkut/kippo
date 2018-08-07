from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import ugettext_lazy as _
from accounts.admin import UserCreatedBaseModelAdmin
from .models import KippoTask, KippoTaskStatus


class KippoTaskAdmin(UserCreatedBaseModelAdmin):
    list_display = (
        'title',
        'category',
        'get_kippoproject_name',
        'get_kippomilestone_title',
        'get_assignee_display_name',
        'get_github_issue_html_url',
        'github_issue_api_url',
    )

    def get_kippoproject_name(self, obj):
        result = ''
        if obj.project and obj.project.name:
            result = obj.project.name
        return result
    get_kippoproject_name.short_description = 'KippoProject'

    def get_kippomilestone_title(self, obj):
        result = ''
        if obj.milestone and obj.milestone.title:
            result = obj.milestone.title
        return result
    get_kippomilestone_title.short_description = 'KippoMilestone'

    def get_assignee_display_name(self, obj):
        result = ''
        if obj and obj.assignee:
            result = obj.assignee.display_name
        return result
    get_assignee_display_name.short_description = 'Assignee'

    def get_github_issue_html_url(self, obj):
        url = ''
        if obj.html_url:
            url = format_html('<a href="{url}">{url}</a>', url=obj.github_issue_html_url)
        return url
    get_github_issue_html_url.short_description = _('Github Issue URL')


class KippoTaskStatusAdmin(UserCreatedBaseModelAdmin):
    list_display = (
        'display_name',
        'effort_date',
        'state',
        'minimum_estimate_days',
        'estimate_days',
        'maximum_estimate_days',
    )


admin.site.register(KippoTask, KippoTaskAdmin)
admin.site.register(KippoTaskStatus, KippoTaskStatusAdmin)
