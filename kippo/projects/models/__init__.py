"""Models of the projects app, one module per domain. Import them from `projects.models`."""

from .definitions import (
    ACTIVE_PROJECT_PHASE_GROUPS,
    ACTIVE_PROJECT_PHASE_RANK,
    DEFAULT_ACTIVE_PROJECT_PHASES,
    DEFAULT_PROJECT_PHASE,
    GITHUB_MANAGER_USERNAME,
    MAX_ASSIGNMENT_PERCENTAGE,
    PARTICIPANT_MAX_START_MONTHS_AHEAD,
    PHASE_COMPLETED,
    PHASE_CONFIDENCE,
    PHASE_UNDER_CONTRACT,
    SALES_PROJECT_PHASES,
    UNASSIGNED_USER_GITHUB_LOGIN_PREFIX,
    UNDER_CONTRACT_REQUIRES_CONTRACT_MSG,
    UNPROCESSABLE_ENTITY_422,
    UNRANKED_PROJECT_PHASE_RANK,
    VALID_COLLECTISSUESPROJECTRESULT_STATES,
    VALID_PROJECT_PHASES,
    _COMPUTE,
)
from .columnset import (
    ProjectColumn,
    ProjectColumnSet,
    category_prefixes_default,
    estimate_prefixes_default,
)
from .category import (
    KippoProjectOrganizationCategory,
    get_default_project_category,
    seed_organization_project_categories,
)
from .project import (
    ActiveKippoProject,
    ActiveKippoProjectManager,
    KippoProject,
    KippoProjectPhaseConfidenceChange,
    KippoProjectStatus,
    SalesKippoProject,
    SalesKippoProjectManager,
    _normalize_slack_channel_name,
    get_target_date_default,
)
from .contract import (
    KippoProjectBillingEntry,
    KippoProjectContract,
)
from .milestone import (
    KippoMilestone,
    cleanup_github_milestones,
)
from .assignment import (
    ProjectAssignmentRate,
    ProjectMonthlyAssignment,
    ProjectParticipant,
    participant_period_error,
)
from .effort import (
    ProjectWeeklyEffort,
    ProjectWeeklyEffortUnlock,
)
from .collectissues import (
    CollectIssuesAction,
    CollectIssuesProjectResult,
)
from .survey import (
    KippoProjectUserMonthlyStatisfactionResult,
    KippoProjectUserStatisfactionResult,
    get_current_month,
)
from .cost import (
    ProjectMonthlyCost,
)

__all__ = [
    "ACTIVE_PROJECT_PHASE_GROUPS",
    "ACTIVE_PROJECT_PHASE_RANK",
    "ActiveKippoProject",
    "ActiveKippoProjectManager",
    "CollectIssuesAction",
    "CollectIssuesProjectResult",
    "DEFAULT_ACTIVE_PROJECT_PHASES",
    "DEFAULT_PROJECT_PHASE",
    "GITHUB_MANAGER_USERNAME",
    "KippoMilestone",
    "KippoProject",
    "KippoProjectBillingEntry",
    "KippoProjectContract",
    "KippoProjectOrganizationCategory",
    "KippoProjectPhaseConfidenceChange",
    "KippoProjectStatus",
    "KippoProjectUserMonthlyStatisfactionResult",
    "KippoProjectUserStatisfactionResult",
    "MAX_ASSIGNMENT_PERCENTAGE",
    "PARTICIPANT_MAX_START_MONTHS_AHEAD",
    "PHASE_COMPLETED",
    "PHASE_CONFIDENCE",
    "PHASE_UNDER_CONTRACT",
    "ProjectAssignmentRate",
    "ProjectColumn",
    "ProjectColumnSet",
    "ProjectMonthlyAssignment",
    "ProjectMonthlyCost",
    "ProjectParticipant",
    "ProjectWeeklyEffort",
    "ProjectWeeklyEffortUnlock",
    "SALES_PROJECT_PHASES",
    "SalesKippoProject",
    "SalesKippoProjectManager",
    "UNASSIGNED_USER_GITHUB_LOGIN_PREFIX",
    "UNDER_CONTRACT_REQUIRES_CONTRACT_MSG",
    "UNPROCESSABLE_ENTITY_422",
    "UNRANKED_PROJECT_PHASE_RANK",
    "VALID_COLLECTISSUESPROJECTRESULT_STATES",
    "VALID_PROJECT_PHASES",
    "_COMPUTE",
    "_normalize_slack_channel_name",
    "category_prefixes_default",
    "cleanup_github_milestones",
    "estimate_prefixes_default",
    "get_current_month",
    "get_default_project_category",
    "get_target_date_default",
    "participant_period_error",
    "seed_organization_project_categories",
]
