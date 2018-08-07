from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import ugettext_lazy as _
from accounts.admin import UserCreatedBaseModelAdmin
from .models import GithubRepository, GithubMilestone, GithubAccessToken, GithubRepositoryLabelsDefinition


class GithubRepositoryAdmin(UserCreatedBaseModelAdmin):
    list_display = (
        'name',
        'project',
        'get_html_url',
        'api_url',
    )

    def get_html_url(self, obj):
        url = ''
        if obj.html_url:
            url = format_html('<a href="{url}"></a>', url=obj.html_url)
        return url
    get_html_url.short_description = _('Repository URL')


class GithubMilestoneAdmin(UserCreatedBaseModelAdmin):
    list_display = (
        'number',
        'get_kippomilestone_title',
        'get_githubrepository_name',
        'get_html_url',
        'api_url',
    )

    def get_kippomilestone_title(self, obj):
        result = ''
        if obj.milestone and obj.milestone.title:
            result = obj.milestone.title
        return result

    def get_githubrepository_name(self, obj):
        result = ''
        if obj.repository and obj.repository.name:
            result = obj.repository.name
        return result

    def get_html_url(self, obj):
        url = ''
        if obj.html_url:
            url = format_html('<a href="{url}">{url}</a>', url=obj.html_url)
        return url
    get_html_url.short_description = _('Milestone URL')


class GithubRepositoryLabelsDefinitionAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'get_label_count',
        'updated_datetime',
        'created_datetime',
    )

    def get_label_count(self, obj):
        result = ''
        if obj.labels:
            result = len(obj.labels)
        return result
    get_label_count.short_description = 'Defined Label Count'


admin.site.register(GithubRepository, GithubRepositoryAdmin)
admin.site.register(GithubMilestone, GithubMilestoneAdmin)
admin.site.register(GithubRepositoryLabelsDefinition, GithubRepositoryLabelsDefinitionAdmin)
