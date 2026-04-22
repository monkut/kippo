from commons.definitions import StringEnumWithChoices


class FeedbackCategories(StringEnumWithChoices):
    BUG = "bug"
    FEATURE = "feature"
    GENERAL = "general"
    OTHER = "other"


class FeedbackReviewActions(StringEnumWithChoices):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    NEEDS_INFO = "needs_info"
    ISSUE_CREATED = "issue_created"


SUPERUSER_ONLY_FIELDS = ("reviewed_datetime", "review_action", "github_issue_url")
