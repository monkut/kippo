"""Shared migration operations used across app migrations."""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import ProjectState


class AlterFieldArrayToJSONB(migrations.AlterField):
    """AlterField that converts a postgres `varchar[]` column to `jsonb`.

    Postgres has no implicit cast from `varchar[]` to `jsonb`, so Django's
    default `ALTER COLUMN ... TYPE jsonb USING <col>::jsonb` fails. On
    postgres, issue the ALTER with `USING to_jsonb(<col>)` instead, which
    converts the array into a JSON array and preserves NULL/empty rows.

    On every other backend (notably sqlite), defer to Django's default
    behavior: sqlite rebuilds the table via `_remake_table`, which handles
    the type change implicitly without needing a USING clause.

    Reverse is a no-op on postgres because `ALTER COLUMN USING` does not
    permit subqueries, so unpacking jsonb back to `varchar[]` would need
    a helper function or manual data rewrite — both are rare in practice.
    """

    def database_forwards(
        self,
        app_label: str,
        schema_editor: BaseDatabaseSchemaEditor,
        from_state: ProjectState,
        to_state: ProjectState,
    ) -> None:
        if schema_editor.connection.vendor == "postgresql":
            table = from_state.apps.get_model(app_label, self.model_name)._meta.db_table
            schema_editor.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{self.name}" TYPE jsonb USING to_jsonb("{self.name}")')
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(
        self,
        app_label: str,
        schema_editor: BaseDatabaseSchemaEditor,
        from_state: ProjectState,
        to_state: ProjectState,
    ) -> None:
        if schema_editor.connection.vendor == "postgresql":
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)
