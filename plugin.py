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

import json
import logging
import os

logger = logging.getLogger("plugins.dispatcharr_timeshift")

# Track if hooks are installed in THIS worker (each uWSGI worker is separate)
_hooks_installed = False

# Prevent recursive worker warmup: each warmup triggers channel_start events
# which load this module in other workers, which would otherwise re-trigger
# warmup, creating an explosion of HTTP loopback requests.
_warmup_attempted = False


def _warmup_workers():
    """
    Force plugin loading in all uWSGI workers via HTTP loopback.

    Dispatcharr v0.24 (commit ddb0328) introduced should_skip_initialization()
    which skips plugin discovery in worker processes (lazy-apps=true). Our
    monkey-patches only get installed when discover_plugins() runs in a worker.
    Connect events trigger discover_plugins via apps.connect.utils.trigger_event().
    We use loopback HTTP to round-robin all workers and fire channel_start.

    Each request opens connection then closes immediately — the provider
    stream is interrupted before any meaningful bandwidth use.

    Cross-process coordination:
        - Per-process flag _warmup_attempted prevents recursive warmup within
          the same worker (loopback fires channel_start which would otherwise
          re-load module and re-trigger warmup).
        - Redis NX lock with 60s TTL ensures only ONE worker actually performs
          warmup across the cluster. Others see the lock and skip immediately.
          Without this, 4 workers × 8 requests = 32 simultaneous loopback
          requests overload uWSGI and ALL workers eventually re-trigger warmup
          when their loopback requests hit.
    """
    global _warmup_attempted

    if _warmup_attempted:
        return
    _warmup_attempted = True

    import urllib.request
    import urllib.error
    import socket
    import threading
    import time

    try:
        from .hooks import _get_plugin_config
        config = _get_plugin_config()
        if not config.get('warmup_on_enable', True):
            logger.info("[Timeshift] Worker warmup disabled by setting (warmup_on_enable=False)")
            return

        # Cluster-wide exclusion: only one worker actually performs warmup.
        # Use Dispatcharr's existing RedisClient (already connected in core.utils).
        # Short TTL (15s) so a recycled worker can re-warmup after a previous run
        # if the first attempt missed some workers.
        try:
            from core.utils import RedisClient
            redis = RedisClient.get_client()
            lock_key = "dispatcharr_timeshift:warmup_lock"
            acquired = redis.set(lock_key, "1", nx=True, ex=15)
            if not acquired:
                logger.debug("[Timeshift] Warmup already running in another process, skipping")
                return
        except Exception as e:
            logger.debug(f"[Timeshift] Redis lock unavailable, proceeding without cluster lock: {e}")

        # Brief delay to let install_hooks() finish in caller and Django settle.
        time.sleep(0.5)

        # Find a user with xc_password and a channel with active streams.
        from apps.accounts.models import User
        from apps.channels.models import Channel

        user = None
        for u in User.objects.all():
            if (u.custom_properties or {}).get('xc_password'):
                user = u
                break
        if not user:
            logger.warning("[Timeshift] Worker warmup skipped: no user with xc_password configured")
            return

        xc_password = user.custom_properties['xc_password']
        channel = Channel.objects.filter(streams__isnull=False).first()
        if not channel:
            logger.warning("[Timeshift] Worker warmup skipped: no channel with streams")
            return

        url = f"http://localhost:5656/live/{user.username}/{xc_password}/{channel.id}.ts"
        logger.info(f"[Timeshift] Warming up uWSGI workers via {url}")

        def _ping_worker(idx):
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=2) as resp:
                    resp.read(1024)  # Minimal read to ensure channel_start fires
            except (socket.timeout, urllib.error.URLError):
                pass  # Expected — we close the connection fast
            except Exception as e:
                logger.debug(f"[Timeshift] Warmup ping {idx} error: {e}")

        # Send 20 sequential requests with 100ms gap. Sequential avoids
        # overloading uWSGI; 20 requests give >99.7% probability that all 4
        # workers are hit (P(miss any worker) ≤ 4 × (3/4)^20 ≈ 1.3%).
        for i in range(20):
            _ping_worker(i)
            time.sleep(0.1)

        logger.info("[Timeshift] Worker warmup complete — hooks should now be active in all workers")

    except Exception as e:
        logger.warning(f"[Timeshift] Worker warmup failed (non-fatal): {e}")


def _auto_install_hooks():
    """
    Install hooks automatically on Django startup.

    Hooks are ALWAYS installed, but they check _is_plugin_enabled() at runtime.
    This allows enabling/disabling the plugin without restart.

    Triggers a background warmup of other uWSGI workers (Dispatcharr v0.24+
    skips plugin discovery in workers, so they need to be woken up via Connect
    events fired by HTTP loopback).
    """
    global _hooks_installed

    if _hooks_installed:
        return

    try:
        from .hooks import install_hooks
        if install_hooks():
            _hooks_installed = True
            logger.info("[Timeshift] Hooks installed (will check enabled state at runtime)")

            # Warm up other uWSGI workers in background (non-blocking).
            # See _warmup_workers() docstring for the v0.24 issue this works around.
            import threading
            threading.Thread(target=_warmup_workers, daemon=True).start()

    except Exception as e:
        logger.error(f"[Timeshift] Auto-install error: {e}")


def _read_plugin_version():
    """
    Read version from plugin.json (single source of truth).

    WHY plugin.json?
        plugin.py self.version was often forgotten during releases,
        causing mismatches. plugin.json is always updated by the
        release process, so it's the authoritative version source.
    """
    try:
        manifest_path = os.path.join(os.path.dirname(__file__), "plugin.json")
        with open(manifest_path, "r") as f:
            return json.load(f).get("version", "0.0.0")
    except Exception:
        return "0.0.0"


class Plugin:
    """
    Main plugin class for Dispatcharr Timeshift.

    Dispatcharr's PluginManager calls run() with action="enable" or "disable"
    when the plugin is toggled in the UI.
    """

    GITHUB_REPO = "cedric-marcoux/dispatcharr_timeshift"

    def __init__(self):
        self.name = "Dispatcharr Timeshift"
        self.version = _read_plugin_version()
        self.description = "Timeshift/catch-up TV support for Xtream Codes providers"
        self.url = "https://github.com/cedric-marcoux/dispatcharr_timeshift"
        self.author = "Cedric Marcoux"
        self.author_url = "https://github.com/cedric-marcoux"

        # Settings fields (version info is prepended dynamically via @property)
        self._settings_fields = [
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
                "id": "warmup_on_enable",
                "type": "boolean",
                "label": "Auto warm-up workers (Dispatcharr v0.24+)",
                "default": True,
                "help_text": (
                    "On Dispatcharr v0.24+, plugins are not loaded in uWSGI workers by default. "
                    "Enable to automatically warm up all workers via HTTP loopback when the plugin "
                    "is enabled. Disable to save brief provider bandwidth (cold-start delay until "
                    "first channel is played by any client)."
                )
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
                "help_text": (
                    "Only used when 'Custom template' is selected. "
                    "Example: {server_url}/streaming/timeshift.php?username={username}&password={password}"
                    "&stream={stream_id}&start={timestamp}&duration={duration} — "
                    "Placeholders: {server_url} {username} {password} {stream_id} {timestamp} (local time YYYY-MM-DD:HH-MM) "
                    "{duration} (minutes from EPG) {start_unix} (Unix epoch) "
                    "{epg_channel_id} {channel_name} {channel_id} {tv_archive_duration} (days) {extension} (ts/m3u8)"
                )
            }
        ]

        # _keepalive: defense-in-depth action subscribed to frequent Connect events.
        # When any of these events fires, Dispatcharr calls pm.discover_plugins() in
        # the worker handling the event, which imports this plugin module and triggers
        # _auto_install_hooks(). This guarantees workers stay patched even if HTTP
        # warmup missed them (e.g. uWSGI worker recycled after timeout).
        self.actions = [
            {
                "id": "_keepalive",
                "label": "Keep hooks active (internal)",
                "description": (
                    "Internal no-op action subscribed to Connect events. "
                    "Ensures plugin module is loaded in every uWSGI worker on Dispatcharr v0.24+."
                ),
                "events": [
                    "channel_start",
                    "channel_stop",
                    "client_connect",
                    "client_disconnect",
                    "epg_refresh",
                ],
            }
        ]

    @property
    def fields(self):
        """
        Dynamic fields: version check info + settings fields.

        WHY @property?
            The version check is lazy: only triggered when the settings page
            loads (PluginManager reads plugin.instance.fields). The result is
            cached 24h in memory, so subsequent loads are instant.
            Zero changes needed in Dispatcharr core.
        """
        version_field = self._build_version_field()
        return [version_field] + self._settings_fields

    def _build_version_field(self):
        """Build the info field showing version status with GitHub link."""
        try:
            from .version_check import check_for_update
            result = check_for_update(self.GITHUB_REPO, self.version)
        except Exception:
            result = {"error": "Version check module unavailable", "current": self.version}

        if result.get("error"):
            return {
                "id": "version_info",
                "type": "info",
                "label": f"Version {result['current']}",
                "help_text": f"Unable to check for updates: {result['error']}",
            }
        elif result.get("has_update"):
            return {
                "id": "version_info",
                "type": "info",
                "label": f"Update available: v{result['latest']}",
                "help_text": (
                    f"You are running v{result['current']}. "
                    f"Version {result['latest']} is available. "
                    f"Download: {result['release_url']}"
                ),
            }
        else:
            return {
                "id": "version_info",
                "type": "info",
                "label": f"Version {result['current']} (latest)",
                "help_text": f"You are running the latest version. Last checked: {result.get('checked_at', 'N/A')}",
            }

    def run(self, action=None, params=None, context=None):
        """
        Execute plugin action.

        Called by PluginManager when:
        - action="enable": Plugin is being enabled
        - action="disable": Plugin is being disabled
        - action="_keepalive": Internal no-op fired by Connect events (Dispatcharr v0.24+)
        """
        global _hooks_installed
        context = context or {}

        if action == "_keepalive":
            # No-op: being called means the plugin module was imported in this
            # worker (via pm.discover_plugins triggered by trigger_event). The
            # module-level _auto_install_hooks() already ran at import time.
            return {"status": "ok"}

        if action == "enable":
            logger.info("[Timeshift] Enabling plugin...")
            from .hooks import install_hooks
            if install_hooks():
                _hooks_installed = True
                # Warm up other uWSGI workers (Dispatcharr v0.24+ doesn't load
                # plugins in workers by default).
                import threading
                threading.Thread(target=_warmup_workers, daemon=True).start()
                return {"status": "ok", "message": "Timeshift plugin enabled"}
            return {"status": "error", "message": "Failed to install hooks"}

        elif action == "disable":
            logger.info("[Timeshift] Disabling plugin...")
            from .hooks import uninstall_hooks
            uninstall_hooks()
            _hooks_installed = False
            return {"status": "ok", "message": "Timeshift plugin disabled"}

        return {"status": "error", "message": f"Unknown action: {action}"}

    def stop(self, context=None):
        """
        Graceful shutdown - called by Dispatcharr v0.19+ on disable/reload/delete.

        Args:
            context (dict, optional): Contains 'settings', 'logger', 'reason', 'actions'
        """
        global _hooks_installed
        reason = context.get("reason", "unknown") if context else "unknown"
        logger.info(f"[Timeshift] Stopping plugin (reason: {reason})...")

        from .hooks import uninstall_hooks
        if uninstall_hooks():
            _hooks_installed = False
            logger.info(f"[Timeshift] Plugin stopped successfully (reason: {reason})")
            return {"status": "ok", "message": f"Timeshift stopped (reason: {reason})"}
        return {"status": "error", "message": "Failed to uninstall hooks"}


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
