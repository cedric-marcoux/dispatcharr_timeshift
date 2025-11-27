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
        self.version = "1.0.1"
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
            logger.info("[Timeshift] Disabling plugin...")
            from .hooks import uninstall_hooks
            uninstall_hooks()
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
