import uuid

from commons.models import UserCreatedBaseModel
from django.db import models
from django.utils.translation import gettext_lazy as _


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

    class Meta:
        unique_together = (("organization", "name"),)
        verbose_name = _("顧客")
        verbose_name_plural = _("顧客")

    def __str__(self) -> str:
        return self.name


class KippoCustomerComplianceCheck(UserCreatedBaseModel):
    """反社チェック (anti-social/compliance check) state for a KippoCustomer.

    Auto-created (one per customer) by a post_save signal with created_by/updated_by
    null and verified=False. The customer is considered verified once `verified` is True.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.OneToOneField(
        "customers.KippoCustomer",
        on_delete=models.CASCADE,
        related_name="compliance_check",
        verbose_name=_("顧客"),
    )
    verified = models.BooleanField(default=False, verbose_name=_("反社チェック済み"))
    verified_datetime = models.DateTimeField(null=True, blank=True, verbose_name=_("反社チェック日時"))
    notes = models.TextField(blank=True, default="", verbose_name=_("メモ"))

    class Meta:
        verbose_name = _("反社チェック")
        verbose_name_plural = _("反社チェック")

    def __str__(self) -> str:
        return f"{self.customer.name} (verified={self.verified})"
