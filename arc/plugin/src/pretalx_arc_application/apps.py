from django.apps import AppConfig


class ArcApplicationConfig(AppConfig):
    name = "pretalx_arc_application"
    verbose_name = "ARC application workflow"

    class PretalxPluginMeta:
        name = "ARC application workflow"
        author = "ARC"
        version = "0.1.0"
        visible = True
        description = (
            "Configures a focused PhD application form, generates application "
            "titles, and prevents interview information from being published."
        )
        category = "CUSTOMIZATION"

    def ready(self):
        from . import signals  # noqa: F401

    def installed(self, event):
        from .schema import configure_event

        configure_event(event)
