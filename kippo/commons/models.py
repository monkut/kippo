from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.db import models
from django.db.models import QuerySet


class UserCreatedBaseModel(models.Model):
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        editable=False,
        related_name="%(app_label)s_%(class)s_created_by",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        editable=False,
        related_name="%(app_label)s_%(class)s_updated_by",
    )
    created_datetime = models.DateTimeField(auto_now_add=True, editable=False)
    updated_datetime = models.DateTimeField(auto_now=True, editable=False)
    closed_datetime = models.DateTimeField(editable=False, null=True)

    class Meta:
        abstract = True


class TimestampedModel(models.Model):
    created_datetime = models.DateTimeField(auto_now_add=True, editable=False)
    updated_datetime = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True


class PersistedFieldSnapshotMixin:
    """Remember the value some fields had when the row was last read from, or written to, the database.

    `PERSISTED_SNAPSHOT_ATTRIBUTES` maps field name -> the instance attribute holding its snapshot.
    A model's save() compares the snapshot with the current value to detect a change, then calls
    `_snapshot_persisted_state(written_fields)`. NOTE: Must be placed BEFORE the Model base class.
    """

    PERSISTED_SNAPSHOT_ATTRIBUTES: dict[str, str] = {}

    def _snapshot_persisted_state(self, fields: Iterable[str] | None = None) -> None:
        # Read loaded values from __dict__ only: touching a deferred attribute would trigger
        # refresh_from_db -> from_db -> this snapshot again (RecursionError on .only() / cascade deletes).
        # A deferred field is snapshotted later, when its lazy load runs refresh_from_db(fields=[...]).
        for field_name, attribute in self.PERSISTED_SNAPSHOT_ATTRIBUTES.items():
            if (fields is None or field_name in fields) and field_name in self.__dict__:
                setattr(self, attribute, self.__dict__[field_name])

    @classmethod
    def from_db(cls, db: str, field_names: list[str], values: list[Any]):  # noqa: ANN206
        instance = super().from_db(db, field_names, values)  # type: ignore[misc]
        instance._snapshot_persisted_state()
        return instance

    def refresh_from_db(self, using: str | None = None, fields: Iterable[str] | None = None, from_queryset: QuerySet | None = None) -> None:
        fields = list(fields) if fields is not None else None
        super().refresh_from_db(using=using, fields=fields, from_queryset=from_queryset)  # type: ignore[misc]
        self._snapshot_persisted_state(fields)
