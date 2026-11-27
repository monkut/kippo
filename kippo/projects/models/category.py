"""Per-organization project categories."""

import uuid

from accounts.models import KippoOrganization, KippoUser
from commons.models import UserCreatedBaseModel
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import QuerySet
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from ..definitions import (
    DEFAULT_PROJECT_CATEGORY_VALUE,
    KIPPOPROJECT_CATEGORY_MAX_LENGTH,
)


class KippoProjectOrganizationCategory(UserCreatedBaseModel):
    """Selectable KippoProject.category value.

    A row with ``organization=None`` is a global default (seeded from
    ``DEFAULT_KIPPOPROJECT_CATEGORIES``); a row with an organization is that
    organization's custom category. ``KippoProject.category`` is a FK here so the
    available categories can optionally be made dynamic per organization (kippo#30 / T08, T20).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.KippoOrganization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="project_categories",
        verbose_name=_("組織"),
        help_text=_("Organization this category belongs to; leave empty for a global default category"),
    )
    key = models.CharField(max_length=KIPPOPROJECT_CATEGORY_MAX_LENGTH, verbose_name=_("キー"))
    label = models.CharField(max_length=128, verbose_name=_("ラベル"))
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name=_("表示順"))
    is_active = models.BooleanField(default=True, verbose_name=_("有効"))
    is_default = models.BooleanField(
        default=False,
        verbose_name=_("デフォルト"),
        help_text=_("Auto-selected category for new projects in this organization (at most one per organization)"),
    )

    class Meta:
        verbose_name = _("プロジェクトカテゴリ")
        verbose_name_plural = _("プロジェクトカテゴリ")
        ordering = ("sort_order", "key")
        constraints = (
            models.UniqueConstraint(fields=("organization", "key"), name="uniq_org_category_key"),
            models.UniqueConstraint(fields=("key",), condition=models.Q(organization__isnull=True), name="uniq_global_category_key"),
            # ラベル: unique within an organization (and among the global defaults). Under copy-on-create
            # (kippo#49) an org OWNS its copy of the defaults, so an org label equal to a global label is
            # expected (globals are only the seed template, never shown alongside org rows) — there is no
            # cross-scope disjointness rule.
            models.UniqueConstraint(fields=("organization", "label"), name="uniq_org_category_label"),
            models.UniqueConstraint(fields=("label",), condition=models.Q(organization__isnull=True), name="uniq_global_category_label"),
        )

    def __str__(self) -> str:
        return self.label

    def clean(self) -> None:
        super().clean()
        if self.is_default:
            if self.organization_id is None:
                raise ValidationError({"is_default": _("Only an organization category can be the default.")})
            if not self.is_active:
                raise ValidationError({"is_default": _("A default category must be active.")})

    def save(self, *args, **kwargs):
        # Enforce a single default per organization in application code (writes funnel through save()
        # from both the API serializer and admin). Setting is_default on one row demotes the others,
        # so the org default can be reassigned with a single PATCH (kippo#50).
        if self.is_default and self.organization_id:
            KippoProjectOrganizationCategory.objects.filter(organization_id=self.organization_id, is_default=True).exclude(pk=self.pk).update(
                is_default=False
            )
        super().save(*args, **kwargs)

    @classmethod
    def get_default_for_organization(cls, organization_id: "uuid.UUID | str") -> "KippoProjectOrganizationCategory | None":
        """The organization's default category: its explicit ``is_default`` row, else its ``other`` copy."""
        return (
            cls.objects.filter(organization_id=organization_id, is_default=True, is_active=True).first()
            or cls.objects.filter(organization_id=organization_id, key=DEFAULT_PROJECT_CATEGORY_VALUE).first()
        )

    @classmethod
    def get_for_organization(cls, organization: KippoOrganization | None) -> QuerySet:
        """Active categories owned by an organization (org-scoped rows only).

        Under copy-on-create (kippo#49) each org owns its own copy of the default set; the global
        (organization=null) rows are only the seed template and are no longer inherited/shown here.
        """
        return cls.objects.filter(is_active=True, organization=organization)

    @classmethod
    def seed_for_organization(cls, organization: KippoOrganization, created_by: KippoUser | None = None) -> int:
        """Copy the active global default categories into org-scoped rows for ``organization``.

        Idempotent: a key already present for the org is skipped, so re-running never duplicates.
        Returns the number of rows created.
        """
        existing_keys = set(cls.objects.filter(organization=organization).values_list("key", flat=True))
        actor = created_by or getattr(organization, "created_by", None)
        to_create = [
            cls(
                organization=organization,
                key=default.key,
                label=default.label,
                sort_order=default.sort_order,
                is_active=default.is_active,
                created_by=actor,
                updated_by=actor,
            )
            for default in cls.objects.filter(organization__isnull=True, is_active=True)
            if default.key not in existing_keys
        ]
        cls.objects.bulk_create(to_create)
        return len(to_create)


def get_default_project_category():
    """PK of the global (organization=null) template default, used as ``KippoProject.category``'s
    field default at instantiation. On create, ``KippoProject.save()`` swaps this for the project's
    own organization's copy of the same key (kippo#49).
    """
    return (
        KippoProjectOrganizationCategory.objects.filter(organization__isnull=True, key=DEFAULT_PROJECT_CATEGORY_VALUE)
        .values_list("pk", flat=True)
        .first()
    )


@receiver(models.signals.post_save, sender=KippoOrganization)
def seed_organization_project_categories(sender: type[KippoOrganization], instance: KippoOrganization, created: bool, raw: bool = False, **kwargs):  # noqa: ARG001, FBT001, FBT002
    """On organization creation, copy the global default categories into org-scoped rows (kippo#49).

    Skipped during fixture loading (``raw=True``) so loaddata controls the exact rows.
    """
    if created and not raw:
        KippoProjectOrganizationCategory.seed_for_organization(instance)
