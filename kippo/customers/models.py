import uuid

import reversion
from commons.models import UserCreatedBaseModel
from django.db import models
from django.utils.translation import gettext_lazy as _


@reversion.register()
class KippoCustomer(UserCreatedBaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.KippoOrganization",
        on_delete=models.CASCADE,
        verbose_name=_("組織"),
    )
    name = models.CharField(max_length=256, verbose_name=_("顧客名"))
    email = models.EmailField(blank=True, default="", verbose_name=_("メールアドレス"))
    phone = models.CharField(max_length=50, blank=True, default="", verbose_name=_("電話番号"))
    website = models.URLField(blank=True, default="", verbose_name=_("ウェブサイト"))
    document_url = models.URLField(
        blank=True,
        default="",
        verbose_name=_("ドキュメントURL"),
        help_text=_("Link to customer-related documents (folder, drive, wiki, etc.)"),
    )
    notes = models.TextField(blank=True, default="", verbose_name=_("メモ"))
    display_as_active = models.BooleanField(
        _("Display as Active"),
        default=True,
        help_text=_("If False, hidden from default admin lists"),
    )

    class Meta:
        unique_together = (("organization", "name"),)
        verbose_name = _("顧客")
        verbose_name_plural = _("顧客")

    def __str__(self) -> str:
        return self.name
