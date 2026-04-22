from commons.models import UserCreatedBaseModel
from django.db import models
from django.utils.translation import gettext_lazy as _

from .definitions import FeedbackCategories, FeedbackReviewActions


class Feedback(UserCreatedBaseModel):
    """User-submitted feedback (bugs, feature requests, general comments)."""

    category = models.CharField(
        max_length=20,
        choices=FeedbackCategories.choices(),
        default=FeedbackCategories.GENERAL.value,
    )
    title = models.CharField(max_length=200)
    comment = models.TextField()
    organization = models.ForeignKey(
        "accounts.KippoOrganization",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="feedback",
    )
    reviewed_datetime = models.DateTimeField(null=True, blank=True)
    review_action = models.CharField(  # noqa: DJ001
        max_length=20,
        choices=FeedbackReviewActions.choices(),
        null=True,
        blank=True,
    )
    github_issue_url = models.URLField(null=True, blank=True)  # noqa: DJ001

    class Meta:
        verbose_name = _("Feedback")
        verbose_name_plural = _("Feedback")
        ordering = ["-created_datetime"]

    def __str__(self) -> str:
        return f"[{self.category}] {self.title}"
