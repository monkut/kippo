from commons.definitions import StringEnumWithChoices

# Query param the org-scoped KippoUser autocomplete pins on its AJAX endpoint; KippoUserAdmin.
# get_search_results reads it to narrow the dropdown to a single organization's members
# (プロジェクトマネージャー on the project admin).
KIPPOUSER_AUTOCOMPLETE_ORGANIZATION_PARAM = "organization"


class AttendanceRecordCategory(StringEnumWithChoices):
    START = "start"
    BREAK_START = "break_start"
    BREAK_END = "break_end"
    END = "end"
