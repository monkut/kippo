"""Reusable model and DRF serializer fields shared across kippo apps."""

from typing import Any

from django import forms
from django.core import exceptions
from django.db import models
from rest_framework import serializers

CSVValue = str | list[str] | None


def _parse_csv(value: CSVValue) -> list[str]:
    """Normalize a CSV string or list[str] into a stripped, empty-filtered list."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    msg = f"Expected str or list[str], got {type(value).__name__}"
    raise exceptions.ValidationError(msg)


def _join_csv(value: CSVValue) -> str:
    """Render a CSV string or list[str] as a canonical comma-joined string."""
    return ",".join(_parse_csv(value))


class CommaSeparatedFormField(forms.CharField):
    """Form-side CharField that renders list[str] model values as CSV text."""

    def prepare_value(self, value: CSVValue) -> str | None:
        if isinstance(value, list):
            return ",".join(value)
        return super().prepare_value(value)


class CommaSeparatedCharField(models.CharField):
    """CharField stored as comma-separated text; Python attribute is list[str].

    DB column type and storage remain a `varchar(max_length)`; the column holds
    a canonical CSV string like `"foo,bar"`. Reading a row from the DB returns
    `["foo", "bar"]` on the model attribute. Whitespace around tags is
    stripped and empty tags are dropped on both read and write.

    Assignment accepts either a `list[str]` or a CSV string -- normalization
    runs on save via `get_prep_value` and on reload via `from_db_value`.
    """

    description = "Comma-separated values stored as text, accessed as list[str]"  # type: ignore[assignment]

    def from_db_value(self, value: str | None, expression: Any, connection: Any) -> list[str]:  # noqa: ANN401
        return _parse_csv(value)

    def to_python(self, value: CSVValue) -> list[str]:
        return _parse_csv(value)

    def get_prep_value(self, value: CSVValue) -> str:
        return _join_csv(value)

    def value_to_string(self, obj: models.Model) -> str:
        return _join_csv(self.value_from_object(obj))

    def formfield(self, **kwargs: Any) -> forms.Field:  # noqa: ANN401
        kwargs.setdefault("form_class", CommaSeparatedFormField)
        return super().formfield(**kwargs)


class CommaSeparatedField(serializers.CharField):
    """DRF serializer field for `CommaSeparatedCharField`.

    Reads the model's list[str] attribute and renders it on the wire as a CSV
    string (`"foo,bar"`); accepts an incoming CSV string from clients
    unchanged -- the model layer normalizes via `get_prep_value` on save.
    Inherits `max_length` / `allow_blank` / `required` validation from
    `serializers.CharField`.
    """

    def to_representation(self, value: CSVValue) -> str:
        return _join_csv(value)
