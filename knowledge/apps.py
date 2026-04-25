from django.apps import AppConfig


class KnowledgeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "knowledge"
    verbose_name = "Knowledge Base"

    def ready(self):
        # Wire up post-save signals (auto-embedding of Q&A rows).
        from . import signals  # noqa: F401
