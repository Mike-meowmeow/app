from django.apps import AppConfig


class CommitteeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'committee'
    verbose_name = 'Committees, interviews, and evaluations'
