from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    name = "projects"

    def ready(self) -> None:
        # Import signal handlers so @receiver decorators register on app startup.
        from projects import signals  # noqa: F401
