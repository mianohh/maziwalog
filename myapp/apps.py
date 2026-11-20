from django.apps import AppConfig


class MyappConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "myapp"
    verbose_name = "MaziwaLog"

    def ready(self):
        # Google OAuth profile hydration signal.
        from . import signals  # noqa: F401
