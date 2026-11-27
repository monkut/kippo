"""Request parameters, messages and field groups shared by the projects admin modules."""

from django.utils.translation import gettext_lazy as _

CLOSE_PROJECT_NO_CONTINUATION_VALUE = "__no_continuation__"


# Shown on the contract inline (not the phase field) when a project is moved into 契約(稼働中) but no 契約
# with a period is submitted. What the gate checks is the period (see _validate_under_contract_gate), and
# the period is pre-filled from the project — so what the user must actually do is fill the 契約 terms:
# an untouched row registers no contract, and the terms are what makes the row register (契約金額 for 固定,
# 料金体系=実績 for an effort contract, whose 契約金額 stays blank). Naming 契約金額 alone was wrong: it is not
# required for an effort contract. Phrased from the admin user's perspective (fill in the 契約) since the
# contract saves together with the project. Attached to the inline formset so Django renders that 契約
# component in its error state.
CONTRACT_REQUIRED_FOR_UNDER_CONTRACT_MSG = _(
    "フェーズを契約(稼働中)にするには、契約(開始日・終了日は必須。固定の場合は契約金額も必須)を入力してください。"
)


# GET/POST param carrying the admin URL to return to after a project add/change. Set by callers
# that send the user into the project add form from elsewhere (e.g. the customer admin's
# "プロジェクトを追加" button) so the save redirects back to where they came from.
RETURN_TO_PARAM = "_return_to"


# Closure/survey fields, only meaningful once a project is closed. Named once so the all-projects
# admin (hides them on /add/) and the active admin (hides them always — active ⇒ never closed)
# stay in sync instead of repeating the literal tuple.
PROJECT_CLOSURE_FIELDS = ("close_comment", "survey_issued")


# GET marker the close-action continuation wizard adds to the /add/ URL (kippo#41). When present, the
# admin keeps the full sectioned add form (so the prefilled parent_project + lead_source + inherited
# fields render and save), instead of the flat required-only plain add.
CONTINUATION_SOURCE_PARAM = "_continuation_source"


CONTINUATION_SOURCE_VALUE = "close"


# Hidden field on the close-confirmation form naming the admin the action was started from (e.g.
# "activekippoproject"). The form always posts to the all-projects changelist (see the template
# comment), so the origin has to travel with the POST for the close to land back where it began.
CLOSE_ORIGIN_PARAM = "_close_origin"


# confidence (%, derived from phase via PHASE_CONFIDENCE) at which a non-closed project must carry a
# positive allocated_staff_days estimate (kippo#41).
FULL_CONFIDENCE = 100


# Query param the org-scoped KippoCustomer autocomplete pins on its AJAX endpoint; KippoCustomerAdmin.
# get_search_results reads it to narrow the dropdown to a single organization's customers.
CUSTOMER_AUTOCOMPLETE_ORGANIZATION_PARAM = "organization"


# Query param the employee-survey プロジェクト autocompletes pin on their AJAX endpoint;
# KippoProjectBaseAdmin.get_search_results reads it to narrow the dropdown to exactly the projects the
# survey form validates against (see survey_project_queryset).
SURVEY_PROJECT_AUTOCOMPLETE_SCOPE_PARAM = "survey_scope"


# 振り返り従業員アンケート (KippoProjectUserStatisfactionResult): real projects only.
SURVEY_SCOPE_RETROSPECTIVE = "retrospective"


# （月）従業員アンケート (KippoProjectUserMonthlyStatisfactionResult): 非案件 rows only.
SURVEY_SCOPE_MONTHLY = "monthly"
