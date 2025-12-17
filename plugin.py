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
        self.version = "1.1.8"
        self.description = "Timeshift/catch-up TV support for Xtream Codes providers"
        self.url = "https://github.com/cedric-marcoux/dispatcharr_timeshift"
        self.author = "Cedric Marcoux"
        self.author_url = "https://github.com/cedric-marcoux"

        self.fields = [
            {
                "id": "timezone",
                "type": "select",
                "label": "Provider Timezone",
                "default": "Europe/Brussels",
                "options": [
                    # UTC
                    {"value": "UTC", "label": "UTC"},
                    # Europe
                    {"value": "Europe/Amsterdam", "label": "Europe/Amsterdam (CET)"},
                    {"value": "Europe/Andorra", "label": "Europe/Andorra (CET)"},
                    {"value": "Europe/Athens", "label": "Europe/Athens (EET)"},
                    {"value": "Europe/Belgrade", "label": "Europe/Belgrade (CET)"},
                    {"value": "Europe/Berlin", "label": "Europe/Berlin (CET)"},
                    {"value": "Europe/Bratislava", "label": "Europe/Bratislava (CET)"},
                    {"value": "Europe/Brussels", "label": "Europe/Brussels (CET)"},
                    {"value": "Europe/Bucharest", "label": "Europe/Bucharest (EET)"},
                    {"value": "Europe/Budapest", "label": "Europe/Budapest (CET)"},
                    {"value": "Europe/Chisinau", "label": "Europe/Chisinau (EET)"},
                    {"value": "Europe/Copenhagen", "label": "Europe/Copenhagen (CET)"},
                    {"value": "Europe/Dublin", "label": "Europe/Dublin (GMT/IST)"},
                    {"value": "Europe/Gibraltar", "label": "Europe/Gibraltar (CET)"},
                    {"value": "Europe/Helsinki", "label": "Europe/Helsinki (EET)"},
                    {"value": "Europe/Istanbul", "label": "Europe/Istanbul (TRT)"},
                    {"value": "Europe/Kaliningrad", "label": "Europe/Kaliningrad (EET)"},
                    {"value": "Europe/Kiev", "label": "Europe/Kiev (EET)"},
                    {"value": "Europe/Lisbon", "label": "Europe/Lisbon (WET)"},
                    {"value": "Europe/Ljubljana", "label": "Europe/Ljubljana (CET)"},
                    {"value": "Europe/London", "label": "Europe/London (GMT/BST)"},
                    {"value": "Europe/Luxembourg", "label": "Europe/Luxembourg (CET)"},
                    {"value": "Europe/Madrid", "label": "Europe/Madrid (CET)"},
                    {"value": "Europe/Malta", "label": "Europe/Malta (CET)"},
                    {"value": "Europe/Minsk", "label": "Europe/Minsk (MSK)"},
                    {"value": "Europe/Monaco", "label": "Europe/Monaco (CET)"},
                    {"value": "Europe/Moscow", "label": "Europe/Moscow (MSK)"},
                    {"value": "Europe/Oslo", "label": "Europe/Oslo (CET)"},
                    {"value": "Europe/Paris", "label": "Europe/Paris (CET)"},
                    {"value": "Europe/Podgorica", "label": "Europe/Podgorica (CET)"},
                    {"value": "Europe/Prague", "label": "Europe/Prague (CET)"},
                    {"value": "Europe/Riga", "label": "Europe/Riga (EET)"},
                    {"value": "Europe/Rome", "label": "Europe/Rome (CET)"},
                    {"value": "Europe/Samara", "label": "Europe/Samara (SAMT)"},
                    {"value": "Europe/San_Marino", "label": "Europe/San_Marino (CET)"},
                    {"value": "Europe/Sarajevo", "label": "Europe/Sarajevo (CET)"},
                    {"value": "Europe/Simferopol", "label": "Europe/Simferopol (MSK)"},
                    {"value": "Europe/Skopje", "label": "Europe/Skopje (CET)"},
                    {"value": "Europe/Sofia", "label": "Europe/Sofia (EET)"},
                    {"value": "Europe/Stockholm", "label": "Europe/Stockholm (CET)"},
                    {"value": "Europe/Tallinn", "label": "Europe/Tallinn (EET)"},
                    {"value": "Europe/Tirane", "label": "Europe/Tirane (CET)"},
                    {"value": "Europe/Vaduz", "label": "Europe/Vaduz (CET)"},
                    {"value": "Europe/Vatican", "label": "Europe/Vatican (CET)"},
                    {"value": "Europe/Vienna", "label": "Europe/Vienna (CET)"},
                    {"value": "Europe/Vilnius", "label": "Europe/Vilnius (EET)"},
                    {"value": "Europe/Volgograd", "label": "Europe/Volgograd (MSK)"},
                    {"value": "Europe/Warsaw", "label": "Europe/Warsaw (CET)"},
                    {"value": "Europe/Zagreb", "label": "Europe/Zagreb (CET)"},
                    {"value": "Europe/Zurich", "label": "Europe/Zurich (CET)"},
                    # America
                    {"value": "America/Anchorage", "label": "America/Anchorage (AKST)"},
                    {"value": "America/Argentina/Buenos_Aires", "label": "America/Buenos_Aires (ART)"},
                    {"value": "America/Bogota", "label": "America/Bogota (COT)"},
                    {"value": "America/Caracas", "label": "America/Caracas (VET)"},
                    {"value": "America/Chicago", "label": "America/Chicago (CST)"},
                    {"value": "America/Denver", "label": "America/Denver (MST)"},
                    {"value": "America/Halifax", "label": "America/Halifax (AST)"},
                    {"value": "America/Havana", "label": "America/Havana (CST)"},
                    {"value": "America/Lima", "label": "America/Lima (PET)"},
                    {"value": "America/Los_Angeles", "label": "America/Los_Angeles (PST)"},
                    {"value": "America/Mexico_City", "label": "America/Mexico_City (CST)"},
                    {"value": "America/Montreal", "label": "America/Montreal (EST)"},
                    {"value": "America/New_York", "label": "America/New_York (EST)"},
                    {"value": "America/Panama", "label": "America/Panama (EST)"},
                    {"value": "America/Phoenix", "label": "America/Phoenix (MST)"},
                    {"value": "America/Santiago", "label": "America/Santiago (CLT)"},
                    {"value": "America/Sao_Paulo", "label": "America/Sao_Paulo (BRT)"},
                    {"value": "America/St_Johns", "label": "America/St_Johns (NST)"},
                    {"value": "America/Toronto", "label": "America/Toronto (EST)"},
                    {"value": "America/Vancouver", "label": "America/Vancouver (PST)"},
                    # Asia
                    {"value": "Asia/Almaty", "label": "Asia/Almaty (ALMT)"},
                    {"value": "Asia/Amman", "label": "Asia/Amman (EET)"},
                    {"value": "Asia/Baghdad", "label": "Asia/Baghdad (AST)"},
                    {"value": "Asia/Baku", "label": "Asia/Baku (AZT)"},
                    {"value": "Asia/Bangkok", "label": "Asia/Bangkok (ICT)"},
                    {"value": "Asia/Beirut", "label": "Asia/Beirut (EET)"},
                    {"value": "Asia/Colombo", "label": "Asia/Colombo (IST)"},
                    {"value": "Asia/Damascus", "label": "Asia/Damascus (EET)"},
                    {"value": "Asia/Dhaka", "label": "Asia/Dhaka (BST)"},
                    {"value": "Asia/Dubai", "label": "Asia/Dubai (GST)"},
                    {"value": "Asia/Ho_Chi_Minh", "label": "Asia/Ho_Chi_Minh (ICT)"},
                    {"value": "Asia/Hong_Kong", "label": "Asia/Hong_Kong (HKT)"},
                    {"value": "Asia/Jakarta", "label": "Asia/Jakarta (WIB)"},
                    {"value": "Asia/Jerusalem", "label": "Asia/Jerusalem (IST)"},
                    {"value": "Asia/Kabul", "label": "Asia/Kabul (AFT)"},
                    {"value": "Asia/Karachi", "label": "Asia/Karachi (PKT)"},
                    {"value": "Asia/Kathmandu", "label": "Asia/Kathmandu (NPT)"},
                    {"value": "Asia/Kolkata", "label": "Asia/Kolkata (IST)"},
                    {"value": "Asia/Kuala_Lumpur", "label": "Asia/Kuala_Lumpur (MYT)"},
                    {"value": "Asia/Kuwait", "label": "Asia/Kuwait (AST)"},
                    {"value": "Asia/Manila", "label": "Asia/Manila (PHT)"},
                    {"value": "Asia/Muscat", "label": "Asia/Muscat (GST)"},
                    {"value": "Asia/Nicosia", "label": "Asia/Nicosia (EET)"},
                    {"value": "Asia/Qatar", "label": "Asia/Qatar (AST)"},
                    {"value": "Asia/Riyadh", "label": "Asia/Riyadh (AST)"},
                    {"value": "Asia/Seoul", "label": "Asia/Seoul (KST)"},
                    {"value": "Asia/Shanghai", "label": "Asia/Shanghai (CST)"},
                    {"value": "Asia/Singapore", "label": "Asia/Singapore (SGT)"},
                    {"value": "Asia/Taipei", "label": "Asia/Taipei (CST)"},
                    {"value": "Asia/Tashkent", "label": "Asia/Tashkent (UZT)"},
                    {"value": "Asia/Tehran", "label": "Asia/Tehran (IRST)"},
                    {"value": "Asia/Tokyo", "label": "Asia/Tokyo (JST)"},
                    {"value": "Asia/Yekaterinburg", "label": "Asia/Yekaterinburg (YEKT)"},
                    # Africa
                    {"value": "Africa/Algiers", "label": "Africa/Algiers (CET)"},
                    {"value": "Africa/Cairo", "label": "Africa/Cairo (EET)"},
                    {"value": "Africa/Casablanca", "label": "Africa/Casablanca (WET)"},
                    {"value": "Africa/Johannesburg", "label": "Africa/Johannesburg (SAST)"},
                    {"value": "Africa/Lagos", "label": "Africa/Lagos (WAT)"},
                    {"value": "Africa/Nairobi", "label": "Africa/Nairobi (EAT)"},
                    {"value": "Africa/Tunis", "label": "Africa/Tunis (CET)"},
                    # Australia & Pacific
                    {"value": "Australia/Adelaide", "label": "Australia/Adelaide (ACST)"},
                    {"value": "Australia/Brisbane", "label": "Australia/Brisbane (AEST)"},
                    {"value": "Australia/Darwin", "label": "Australia/Darwin (ACST)"},
                    {"value": "Australia/Hobart", "label": "Australia/Hobart (AEST)"},
                    {"value": "Australia/Melbourne", "label": "Australia/Melbourne (AEST)"},
                    {"value": "Australia/Perth", "label": "Australia/Perth (AWST)"},
                    {"value": "Australia/Sydney", "label": "Australia/Sydney (AEST)"},
                    {"value": "Pacific/Auckland", "label": "Pacific/Auckland (NZST)"},
                    {"value": "Pacific/Fiji", "label": "Pacific/Fiji (FJT)"},
                    {"value": "Pacific/Honolulu", "label": "Pacific/Honolulu (HST)"},
                ],
                "help_text": "Timezone for timestamp conversion (must match your XC provider's timezone)"
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
            },
            {
                "id": "debug_mode",
                "type": "boolean",
                "label": "Debug Mode",
                "default": False,
                "help_text": "Enable ultra-verbose logging for troubleshooting (check Dispatcharr logs)"
            },
            {
                "id": "url_format",
                "type": "select",
                "label": "Catchup URL Format",
                "default": "auto",
                "options": [
                    {"value": "auto", "label": "Auto-detect (A → B fallback)"},
                    {"value": "format_a", "label": "Format A (query string: timeshift.php?...)"},
                    {"value": "format_b", "label": "Format B (path: /timeshift/user/pass/...)"},
                    {"value": "custom", "label": "Custom template"}
                ],
                "help_text": "URL format for timeshift requests. Auto-detect works for most providers."
            },
            {
                "id": "custom_url_template",
                "type": "string",
                "label": "Custom URL Template",
                "default": "",
                "help_text": "Only used when 'Custom template' is selected. Placeholders: {server_url}, {username}, {password}, {stream_id}, {timestamp}, {duration}"
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
            # Note: Dispatcharr only toggles the 'enabled' flag in DB, it doesn't call this.
            # Hooks remain installed and check enabled state at runtime.
            logger.info("[Timeshift] Plugin disabled (hooks check enabled state at runtime)")
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
