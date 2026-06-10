"""Signal handlers for the customers app.

Currently wires:

- `post_save` on `KippoCustomer` → auto-create the related
  `KippoCustomerComplianceCheck` (反社チェック) when one does not yet exist. The
  auto-created row has `created_by`/`updated_by` null and `verified=False` (#28).
"""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from customers.models import KippoCustomer, KippoCustomerComplianceCheck


@receiver(post_save, sender=KippoCustomer)
def handle_create_compliance_check(
    sender: type[KippoCustomer],  # noqa: ARG001 — Django signal handler signature
    instance: KippoCustomer,
    created: bool,
    **kwargs,  # noqa: ARG001 — Django signal kwargs (raw, using, update_fields, etc.)
) -> None:
    """Ensure every KippoCustomer has a KippoCustomerComplianceCheck.

    The auto-created check is unverified (`verified=False`) and has null
    `created_by`/`updated_by` — it represents a not-yet-performed 反社チェック.
    """
    if not created:
        return
    KippoCustomerComplianceCheck.objects.get_or_create(customer=instance)
