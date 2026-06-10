from django.apps import AppConfig


class CustomersConfig(AppConfig):
    name = "customers"
    verbose_name = "顧客"

    def ready(self) -> None:
        # Import signal handlers so @receiver decorators register on app startup.
        from customers import signals  # noqa: F401
