"""
Dispatcharr Timeshift Plugin

Adds timeshift/catch-up TV support for Xtream Codes providers,
allowing users to watch past TV programs (typically up to 7 days).

GitHub: https://github.com/cedric-marcoux/dispatcharr_timeshift

AUTO-INSTALL ON STARTUP:
    This module auto-installs hooks when loaded if the plugin is enabled.
    Dispatcharr's PluginManager imports this module on startup, triggering
    the auto-install code at the bottom of this file.

    IMPORTANT - uWSGI Multi-Worker Architecture:
    Dispatcharr runs with multiple uWSGI workers (separate processes).
    Each worker has its own memory space, so hooks must be installed
    in EACH worker independently.
"""

import logging

logger = logging.getLogger("plugins.dispatcharr_timeshift")

# Track if hooks are installed in THIS worker (each uWSGI worker is separate)
_hooks_installed = False


def _auto_install_hooks():
    """
    Install hooks automatically on Django startup.

    Hooks are ALWAYS installed, but they check _is_plugin_enabled() at runtime.
    This allows enabling/disabling the plugin without restart.
    """
    global _hooks_installed

    if _hooks_installed:
        return

    try:
        from .hooks import install_hooks
        if install_hooks():
            _hooks_installed = True
            logger.info("[Timeshift] Hooks installed (will check enabled state at runtime)")

    except Exception as e:
        logger.error(f"[Timeshift] Auto-install error: {e}")


class Plugin:
    """
    Main plugin class for Dispatcharr Timeshift.

    Dispatcharr's PluginManager calls run() with action="enable" or "disable"
    when the plugin is toggled in the UI.
    """

    def __init__(self):
        self.name = "Dispatcharr Timeshift"
        self.version = "1.1.2"
        self.description = "Timeshift/catch-up TV support for Xtream Codes providers"
        self.url = "https://github.com/cedric-marcoux/dispatcharr_timeshift"
        self.author = "Cedric Marcoux"
        self.author_url = "https://github.com/cedric-marcoux"

        self.fields = [
            {
                "id": "timezone",
                "type": "string",
                "label": "Provider Timezone",
                "default": "Europe/Brussels",
                "help_text": "Timezone for timestamp conversion (IANA format, e.g. Europe/Brussels, America/New_York)"
            },
            {
                "id": "language",
                "type": "select",
                "label": "EPG Language",
                "default": "en",
                "options": [
                    {"value": "bg", "label": "Български (Bulgarian)"},
                    {"value": "cs", "label": "Čeština (Czech)"},
                    {"value": "da", "label": "Dansk (Danish)"},
                    {"value": "de", "label": "Deutsch"},
                    {"value": "el", "label": "Ελληνικά (Greek)"},
                    {"value": "en", "label": "English"},
                    {"value": "es", "label": "Español"},
                    {"value": "et", "label": "Eesti (Estonian)"},
                    {"value": "fi", "label": "Suomi (Finnish)"},
                    {"value": "fr", "label": "Français"},
                    {"value": "hr", "label": "Hrvatski (Croatian)"},
                    {"value": "hu", "label": "Magyar (Hungarian)"},
                    {"value": "it", "label": "Italiano"},
                    {"value": "lt", "label": "Lietuvių (Lithuanian)"},
                    {"value": "lv", "label": "Latviešu (Latvian)"},
                    {"value": "nl", "label": "Nederlands"},
                    {"value": "no", "label": "Norsk (Norwegian)"},
                    {"value": "pl", "label": "Polski (Polish)"},
                    {"value": "pt", "label": "Português"},
                    {"value": "ro", "label": "Română (Romanian)"},
                    {"value": "ru", "label": "Русский (Russian)"},
                    {"value": "sk", "label": "Slovenčina (Slovak)"},
                    {"value": "sl", "label": "Slovenščina (Slovenian)"},
                    {"value": "sr", "label": "Српски (Serbian)"},
                    {"value": "sv", "label": "Svenska (Swedish)"},
                    {"value": "tr", "label": "Türkçe (Turkish)"},
                    {"value": "uk", "label": "Українська (Ukrainian)"},
                ],
                "help_text": "Language code for EPG data (ISO 639-1)"
            }
        ]

        # No custom actions needed
        self.actions = []

    def run(self, action=None, params=None, context=None):
        """
        Execute plugin action.

        Called by PluginManager when:
        - action="enable": Plugin is being enabled
        - action="disable": Plugin is being disabled
        """
        context = context or {}

        if action == "enable":
            logger.info("[Timeshift] Enabling plugin...")
            from .hooks import install_hooks
            if install_hooks():
                return {"status": "ok", "message": "Timeshift plugin enabled"}
            return {"status": "error", "message": "Failed to install hooks"}

        elif action == "disable":
            # [DISABLED v1.1.2] Dispatcharr ne call jamais run("disable")
            # Il toggle juste le flag 'enabled' en DB. Hooks check enabled state at runtime.
            logger.info("[Timeshift] Plugin disabled (hooks remain installed, check enabled at runtime)")
            return {"status": "ok", "message": "Timeshift plugin disabled"}

        return {"status": "error", "message": f"Unknown action: {action}"}


# Auto-install hooks when this module is imported (on Django startup)
# This runs once per uWSGI worker when PluginManager discovers this plugin
try:
    import django
    if django.apps.apps.ready:
        _auto_install_hooks()
    else:
        # Django not ready yet, use signal to install on first request
        from django.core.signals import request_finished

        def _on_first_request(sender, **kwargs):
            _auto_install_hooks()
            request_finished.disconnect(_on_first_request)

        request_finished.connect(_on_first_request)
except Exception:
    pass
