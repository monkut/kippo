"""Shared GitHub issue label parsing.

Canonical home for the label-parsing helpers that were previously duplicated in
``octocat.functions`` and ``tasks.functions``. These depend only on
``ghorgs.wrappers.GithubIssue`` and ``django.conf.settings`` — no octocat/tasks
model imports — so there is no circular-import risk.
"""

import logging
from math import ceil

from django.conf import settings
from ghorgs.wrappers import GithubIssue

logger = logging.getLogger(__name__)


class GithubIssuePrefixedLabel:
    def __init__(self, label: object, prefix_delim: str = ":") -> None:
        self.label = label
        self.prefix_delim = prefix_delim

        # https://developer.github.com/v3/issues/labels/#get-a-single-label
        label_attributes = ("id", "node_id", "url", "name", "color", "default")
        for attrname in label_attributes:
            attrvalue = getattr(label, attrname)
            setattr(self, attrname, attrvalue)

    @property
    def prefix(self):
        return self.name.split(self.prefix_delim)[0]

    @property
    def value(self):
        return self.name.split(self.prefix_delim)[-1]


def get_github_issue_estimate_label(
    issue: GithubIssue,
    prefix: str = settings.DEFAULT_GITHUB_ISSUE_LABEL_ESTIMATE_PREFIX,
    day_workhours: int = settings.DAY_WORKHOURS,
) -> int:
    """
    Parse the estimate label into an estimate value
    Estimate labels follow the scheme: {prefix}N{suffix}
    WHERE:
    - {prefix} estimate label identifier
    - N is a positive integer representing number of days
    - {suffix} one of ('d', 'day', 'days', 'h', 'hour', 'hours')
    - If multiple estimate labels are defined the larger value will be used
    - If no suffix is given, 'days' will be assumed

    .. note::

        Only integer values are supported.
        (fractional days are not represented at the moment)


    :param issue: github issue object
    :param prefix: This identifies the github issue label as being an
    :param day_workhours: Number of hours in the workday
    :return: parsed estimate result in days
    """
    estimate = None
    valid_label_suffixes = ("d", "day", "days", "h", "hour", "hours")
    for label in issue.labels:
        if label.name.startswith(prefix):
            estimate_str_value = label.name.split(prefix)[-1].strip()
            for suffix in valid_label_suffixes:
                if estimate_str_value.endswith(suffix):  # d = days, h = hours
                    estimate_str_value = estimate_str_value.split(suffix)[0]

            try:
                candidate_estimate = int(estimate_str_value)
            except ValueError:
                logger.exception(f"Invalid estimate value cannot convert to int() estimate_str_value={estimate_str_value}, label.name={label.name}")
                candidate_estimate = None

            if candidate_estimate:
                if label.name.endswith(("h", "hour", "hours")):
                    # all estimates are normalized to days
                    # if hours convert to a days
                    candidate_estimate = int(ceil(candidate_estimate / day_workhours))

                if estimate and candidate_estimate:
                    if candidate_estimate > estimate:
                        logger.warning(
                            f"multiple estimate labels found for issue({issue}), using the larger value: {estimate} -> {candidate_estimate}"
                        )
                        estimate = candidate_estimate
                else:
                    estimate = candidate_estimate

    return estimate


def build_latest_comment(issue: GithubIssue) -> str:
    latest_comment = ""
    if issue.latest_comment_body:
        latest_comment = f"{issue.latest_comment_created_by} [ {issue.latest_comment_created_at} ] {issue.latest_comment_body}"
    return latest_comment


def get_github_issue_category_label(issue: GithubIssue, prefix: str = settings.DEFAULT_GITHUB_ISSUE_LABEL_CATEGORY_PREFIX) -> str:
    """
    Parse the category label into the category value
    Category Labels follow the scheme:
        category:CATEGORY_NAME
        WHERE:
            CATEGORY_NAME should match the VALID_TASK_CATEGORIES value in models.py
    :param issue: github issue object
    :param prefix: This identifies the github issue label as being a category
    :return: parsed category result
    """
    category = None
    for label in issue.labels:
        if label.name.startswith(prefix):
            if category:
                logger.warning(
                    f"Multiple Category labels applied on issue: "
                    f"{issue.html_url}, "
                    f"prefix={prefix}, "
                    f"category={category}, "
                    f"label.name={label.name} "
                    "-- initial category will be used!"
                )
                continue
            category = label.name.split(prefix)[-1].strip()
    return category


def get_github_issue_prefixed_labels(issue: GithubIssue, prefix_delim: str = ":") -> list[GithubIssuePrefixedLabel]:
    """Process a label in the format of a prefix/value"""
    prefixed_labels = []
    for label in issue.labels:
        prefixed_label = GithubIssuePrefixedLabel(label, prefix_delim=prefix_delim)
        prefixed_labels.append(prefixed_label)
    return prefixed_labels


def get_tags_from_prefixedlabels(prefixed_labels: list[GithubIssuePrefixedLabel]) -> list[dict[str, str]]:
    tags = [{"name": label.prefix, "value": label.value} for label in prefixed_labels]
    return tags
