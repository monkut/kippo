"""Project phase definitions and limits shared by the projects models."""

from django.conf import settings
from django.utils.translation import gettext_lazy as _

# Sentinel for effort/holiday inputs the REST API list endpoints precompute in bulk and inject,
# letting a single grouped query replace the per-row aggregate/PublicHoliday lookups while the
# admin/standalone callers keep their original (un-injected) behavior.
_COMPUTE = object()


UNASSIGNED_USER_GITHUB_LOGIN_PREFIX = settings.UNASSIGNED_USER_GITHUB_LOGIN_PREFIX


GITHUB_MANAGER_USERNAME = settings.GITHUB_MANAGER_USERNAME


UNPROCESSABLE_ENTITY_422 = 422


MAX_ASSIGNMENT_PERCENTAGE = 100


# Project pipeline status (kippo#36 / T09). The field is still named `phase` (verbose_name フェーズ); the
# values are the sales/delivery status. `confidence` is derived from `phase` via PHASE_CONFIDENCE (no longer
# user-editable). The old anon-project value is retired — non-projects are identified by category=="non-project".
DEFAULT_PROJECT_PHASE = "proposing-low"


PHASE_UNDER_CONTRACT = "under-contract"


# Terminal delivery phase. The close action stamps this on the project being closed, and manually
# selecting it on the change form re-routes the user into the close wizard (see admin).
PHASE_COMPLETED = "completed"


# Shared by KippoProject.clean() (admin) and KippoProjectSerializer (API) so the gate message stays
# in one place (and translated) across both layers.
UNDER_CONTRACT_REQUIRES_CONTRACT_MSG = _("フェーズを契約(稼働中)にするには、開始日・終了日のある契約を保存してください。")


VALID_PROJECT_PHASES = (
    ("keep-in-touch", "KIT"),
    ("proposing-low", _("提案(低)")),
    ("proposing-mid", _("提案(中)")),
    ("proposing-high", _("提案(高)")),
    ("verbal-order", _("口頭受注")),
    (PHASE_UNDER_CONTRACT, _("契約(稼働中)")),
    (PHASE_COMPLETED, _("完了")),
    ("lost", _("失注")),
)


# Changelist / プロジェクト状況 display order, top group first: the contracted block (契約(稼働中)
# plus the unclosed 完了 rows) shares the top rank, then 口頭受注, then the 提案 phases 高 → 中 → 低.
# Rank leads the ordering instead of 確度 because confidence is user-overridable, so a 提案(低) row
# stamped 99 would otherwise outrank 口頭受注 (kiconiaworks/kippo#56). Phases left unranked
# (KIT / 失注) sort after every ranked one.
ACTIVE_PROJECT_PHASE_GROUPS = (
    (PHASE_UNDER_CONTRACT, PHASE_COMPLETED),
    ("verbal-order",),
    ("proposing-high",),
    ("proposing-mid",),
    ("proposing-low",),
)


ACTIVE_PROJECT_PHASE_RANK = {phase: rank for rank, group in enumerate(ACTIVE_PROJECT_PHASE_GROUPS) for phase in group}


UNRANKED_PROJECT_PHASE_RANK = len(ACTIVE_PROJECT_PHASE_GROUPS)


# Phases pre-selected on the active-project admin changelist when the フェーズ filter has no query
# param — the ranked pipeline above. See projects.filters.PhaseMultiSelectListFilter.
# 完了 is included deliberately: ActiveKippoProjectManager already drops closed projects
# (is_closed=True / display_as_active=False), so the only 完了 rows this surfaces are the ones
# stamped 完了 without being closed — the inconsistent state we want visible, and the state whose
# row must stay selectable for the close action.
DEFAULT_ACTIVE_PROJECT_PHASES = tuple(ACTIVE_PROJECT_PHASE_RANK)


# Pre-contract sales-pipeline phases — everything before delivery (under-contract) and before a
# terminal outcome (completed/lost). Consumed by SalesKippoProjectManager (プロジェクト(営業中)).
SALES_PROJECT_PHASES = ("keep-in-touch", "proposing-low", "proposing-mid", "proposing-high", "verbal-order")


# phase -> confidence (確度).
PHASE_CONFIDENCE = {
    "keep-in-touch": 0,  # KIT = "keep in touch": proposal did not succeed
    "proposing-low": 30,
    "proposing-mid": 80,
    "proposing-high": 90,
    "verbal-order": 99,
    "under-contract": 100,
    "completed": 100,
    "lost": 0,
}


# kippo#75: participants can be added only when the project starts within this many months of today.
PARTICIPANT_MAX_START_MONTHS_AHEAD = 6


VALID_COLLECTISSUESPROJECTRESULT_STATES = (("processing", "processing"), ("complete", "complete"))
