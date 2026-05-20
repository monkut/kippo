class ProjectColumnSetError(Exception):
    pass


class ProjectDatesError(Exception):
    pass


class TaskStatusError(Exception):
    pass


class GithubMilestoneAlreadyExistsError(Exception):
    pass


class ProjectStartDateRequiredError(ValueError):
    """Raised when a forecast/suggest operation is requested for a project with no start_date."""


class SlackChannelNotFoundError(Exception):
    """Raised when a project's configured Slack channel cannot be found in the workspace."""
