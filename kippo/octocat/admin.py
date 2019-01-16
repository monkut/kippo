import logging

from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.translation import ugettext_lazy as _
from ghorgs.managers import GithubOrganizationManager
from accounts.admin import UserCreatedBaseModelAdmin, AllowIsStaffAdminMixin
from .models import GithubRepository, GithubMilestone, GithubRepositoryLabelSet


logger = logging.getLogger(__name__)


class GithubRepositoryAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
    list_display = (
        'name',
        'get_label_set_name',
        'get_html_url',
        'api_url',
    )
    actions = (
        'update_labels',
    )

    def get_label_set_name(self, obj):
        result = ''
        if obj.label_set:
            result = obj.label_set.name
        return result
    get_label_set_name.short_description = 'Label Set'

    def update_labels(self, request, queryset):
        delete = False  # Do not delete existing labels atm
        created_labels = []
        deleted_labels = []
        for kippo_repository in queryset:
            if not kippo_repository.label_set:
                msg = f'No GithubRepositoryLabelSet defined for ({kippo_repository.name}) cannot update labels!'
                self.message_user(request, msg, level=messages.ERROR)
            else:
                github_organization_name = kippo_repository.organization.github_organization_name
                githubaccesstoken = kippo_repository.organization.githubaccesstoken
                github_manager = GithubOrganizationManager(organization=github_organization_name,
                                                           token=githubaccesstoken.token)
                repository_name_filter = (kippo_repository.name, )
                logger.debug(f'repository_name_filter: {repository_name_filter}')
                for ghorgs_repository in github_manager.repositories(names=repository_name_filter):
                    existing_label_names = [label['name']for label in ghorgs_repository.labels]

                    # get label definitions
                    defined_label_names = [l['name'] for l in kippo_repository.label_set.labels]
                    for label_definition in kippo_repository.label_set.labels:
                        ghorgs_repository.create_label(label_definition['name'],
                                                       label_definition['description'],
                                                       label_definition['color'])
                        created_labels.append(label_definition)

                    if delete:
                        undefined_label_names = set(existing_label_names) - set(defined_label_names)
                        for label_name in undefined_label_names:
                            ghorgs_repository.delete_label(label_name)
                            deleted_labels.append(label_name)
                msg = f'({kippo_repository.name}) Labels updated using: {kippo_repository.label_set.name}'
                self.message_user(request, msg, level=messages.INFO)
    update_labels.short_description = "Update Repository Labels"

    def get_html_url(self, obj):
        url = ''
        if obj.html_url:
            url = format_html('<a href="{url}"></a>', url=obj.html_url)
        return url
    get_html_url.short_description = _('Repository URL')


class GithubMilestoneAdmin(AllowIsStaffAdminMixin, UserCreatedBaseModelAdmin):
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


class GithubRepositoryLabelSetAdmin(AllowIsStaffAdminMixin, admin.ModelAdmin):
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
admin.site.register(GithubRepositoryLabelSet, GithubRepositoryLabelSetAdmin)
