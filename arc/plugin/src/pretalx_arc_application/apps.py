from django.apps import AppConfig


class ArcApplicationConfig(AppConfig):
    name = "pretalx_arc_application"
    verbose_name = "Recruitment application workflow"

    class PretalxPluginMeta:
        name = "Recruitment application workflow"
        author = "Recruitment team"
        version = "0.1.0"
        visible = True
        description = (
            "Configures a focused PhD application form, generates application "
            "titles, and prevents interview information from being published."
        )
        category = "CUSTOMIZATION"

    def ready(self):
        from . import signals  # noqa: F401
        from .storage import install_private_storage

        install_private_storage()

        from .workflow import install_workflow

        install_workflow()

        from .emails import install_emails
        from .decisions import install_decisions

        install_emails()
        install_decisions()

        from .manual_mail import install_manual_mail

        install_manual_mail()

    def installed(self, event):
        from .schema import configure_event

        configure_event(event)
