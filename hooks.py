"""
Dispatcharr Timeshift Plugin - Hooks

Implements timeshift via monkey-patching (no modification to Dispatcharr source):
1. Patches xc_get_live_streams to add tv_archive and use provider's stream_id
2. Patches stream_xc to find channels by provider stream_id (for live streaming)
3. Patches xc_get_epg to find channels by provider stream_id (for EPG/timeshift data)
4. Patches generate_epg to convert XMLTV timestamps to local timezone (IPTVX fix)
5. Patches URLResolver.resolve to intercept /timeshift/ URLs

RUNTIME ENABLE/DISABLE:
    Hooks are installed once at startup (regardless of plugin enabled state).
    Each hook checks _is_plugin_enabled() at runtime before executing its logic.
    This allows enabling/disabling the plugin without restarting Dispatcharr.

    Why this approach?
    - Dispatcharr's PluginManager only toggles the 'enabled' flag in database
    - It does NOT call plugin.run("enable") or plugin.run("disable")
    - So we can't rely on those callbacks to install/uninstall hooks dynamically
    - Instead, hooks are always installed but check enabled state per-request

WHY MONKEY-PATCHING?
    We tried several approaches before settling on this:

    1. URL pattern injection (urlpatterns.insert) - FAILED
       Dispatcharr has a catch-all pattern "<path:unused_path>" that matches
       everything. Even inserting before it didn't work reliably.

    2. Middleware - FAILED
       Middleware runs after URL resolution, so the catch-all already matched.

    3. ROOT_URLCONF replacement - FAILED
       Django caches settings at startup, so changing ROOT_URLCONF had no effect.

    4. URLResolver.resolve patching - WORKS!
       By patching the resolve() method on the URLResolver class, we intercept
       URL resolution BEFORE any patterns are checked.

CRITICAL: stream_id in API response
    iPlayTV uses the stream_id from get_live_streams API for BOTH:
    - Live streaming: /live/user/pass/{stream_id}.ts
    - Timeshift: /timeshift/user/pass/.../stream_id.ts

    We MUST change stream_id to provider's ID so timeshift works.
    But this breaks live streaming because Dispatcharr's stream_xc looks up by internal ID.

    Solution: Also patch stream_xc to first try provider stream_id lookup.

GitHub: https://github.com/cedric-marcoux/dispatcharr_timeshift
"""

import re
import logging

logger = logging.getLogger("plugins.dispatcharr_timeshift.hooks")

# Store original functions for potential restoration
_original_xc_get_live_streams = None
_original_stream_xc = None
_original_xc_get_epg = None
_original_generate_epg = None
_original_url_callbacks = {}
_original_resolve = None


def _get_plugin_config():
    """
    Get plugin configuration from database.

    Returns all plugin settings configured in plugin UI.
    Used by multiple hooks to avoid duplicating config loading code.

    Returns:
        dict: {
            'timezone': str,
            'language': str,
            'debug_mode': bool,
            'url_format': str ('auto', 'format_a', 'format_b', 'custom'),
            'custom_url_template': str
        }
    """
    defaults = {
        'timezone': 'Europe/Brussels',
        'language': 'en',
        'debug_mode': False,
        'url_format': 'auto',
        'custom_url_template': '',
        'warmup_on_enable': True,
        'xmltv_prev_days_override': 0,
    }
    try:
        from apps.plugins.models import PluginConfig
        config = PluginConfig.objects.filter(key='dispatcharr_timeshift').first()
        if config and config.settings:
            try:
                xmltv_override = int(config.settings.get('xmltv_prev_days_override', 0))
            except (TypeError, ValueError):
                xmltv_override = 0
            return {
                'timezone': config.settings.get('timezone', 'Europe/Brussels').strip(),
                'language': config.settings.get('language', 'en').strip(),
                'debug_mode': bool(config.settings.get('debug_mode', False)),
                'url_format': config.settings.get('url_format', 'auto').strip(),
                'custom_url_template': config.settings.get('custom_url_template', '').strip(),
                'warmup_on_enable': bool(config.settings.get('warmup_on_enable', True)),
                'xmltv_prev_days_override': max(0, min(xmltv_override, 30)),
            }
    except Exception:
        pass
    return defaults


def _is_plugin_enabled():
    """
    Check if plugin is enabled in database.

    Called at runtime by each patched function to determine if timeshift
    logic should execute. This enables hot enable/disable without restart.

    Returns:
        bool: True if plugin is enabled, False otherwise
    """
    try:
        from apps.plugins.models import PluginConfig
        config = PluginConfig.objects.get(key='dispatcharr_timeshift')
        return config.enabled
    except Exception:
        return False


_provider_archive_days_cache = {'value': None, 'expires_at': 0}


def _compute_provider_archive_days():
    """
    Compute max tv_archive_duration across XC streams with catch-up enabled.

    Used to auto-set XMLTV `prev_days` so the EPG grid covers the full
    catch-up window the provider supports. The value is stable across M3U
    syncs, so we cache it for 5 minutes per process to avoid scanning streams
    on every XMLTV download (xmltv.php is called on a schedule by clients).

    Returns:
        int: max provider tv_archive_duration in days, capped at 30.
             0 if no XC stream advertises tv_archive=1 or on any error.
    """
    import time
    now_ts = time.time()
    cached = _provider_archive_days_cache
    if cached['value'] is not None and cached['expires_at'] > now_ts:
        return cached['value']

    max_days = 0
    try:
        from apps.channels.models import Stream
        # JSONField filtering: tv_archive may be int 1 or string "1"
        streams = Stream.objects.filter(
            m3u_account__account_type='XC'
        ).only('custom_properties')
        for stream in streams:
            props = stream.custom_properties or {}
            if props.get('tv_archive') not in (1, '1'):
                continue
            try:
                d = int(props.get('tv_archive_duration', 0) or 0)
            except (TypeError, ValueError):
                continue
            if d > max_days:
                max_days = d
    except Exception as e:
        logger.warning(f"[Timeshift] Could not compute provider archive days: {e}")
        max_days = 0

    # Dispatcharr core caps prev_days at 30 (validated on input)
    max_days = max(0, min(max_days, 30))
    cached['value'] = max_days
    cached['expires_at'] = now_ts + 300  # 5-minute TTL
    return max_days


def install_hooks():
    """
    Install all timeshift hooks.

    Returns:
        bool: True if successful, False otherwise
    """
    logger.info("[Timeshift] Installing hooks...")

    try:
        _patch_xc_get_live_streams()
        _patch_stream_xc()
        _patch_xc_get_epg()
        _patch_generate_epg()
        _patch_url_resolver()
        logger.info("[Timeshift] All hooks installed successfully")
        return True
    except Exception as e:
        logger.error(f"[Timeshift] Failed to install hooks: {e}", exc_info=True)
        return False


def uninstall_hooks():
    """
    Restore all original functions for graceful shutdown.

    Called by Plugin.stop() when Dispatcharr v0.19+ disables, reloads, or
    deletes the plugin. Reverses all monkey-patches applied by install_hooks().

    Returns:
        bool: True if successful, False otherwise
    """
    global _original_xc_get_live_streams, _original_stream_xc
    global _original_xc_get_epg, _original_generate_epg
    global _original_url_callbacks, _original_resolve

    logger.info("[Timeshift] Uninstalling hooks...")

    try:
        # 1. Restore xc_get_live_streams
        if _original_xc_get_live_streams:
            from apps.output import views as output_views
            output_views.xc_get_live_streams = _original_xc_get_live_streams
            _original_xc_get_live_streams = None
            logger.info("[Timeshift] Restored xc_get_live_streams")

        # 2. Restore stream_xc module function + URL pattern callbacks
        if _original_stream_xc:
            from apps.proxy import views as proxy_views
            proxy_views.stream_xc = _original_stream_xc
            # Restore URL pattern callbacks (Django stores references at import time)
            try:
                import dispatcharr.urls as main_urls
                for pattern in main_urls.urlpatterns:
                    if id(pattern) in _original_url_callbacks:
                        pattern.callback = _original_url_callbacks[id(pattern)]
                _original_url_callbacks.clear()
            except Exception as e:
                logger.warning(f"[Timeshift] Could not restore URL callbacks: {e}")
            _original_stream_xc = None
            logger.info("[Timeshift] Restored stream_xc")

        # 3. Restore xc_get_epg
        if _original_xc_get_epg:
            from apps.output import views as output_views
            output_views.xc_get_epg = _original_xc_get_epg
            _original_xc_get_epg = None
            logger.info("[Timeshift] Restored xc_get_epg")

        # 4. Restore generate_epg
        if _original_generate_epg:
            from apps.output import views as output_views
            output_views.generate_epg = _original_generate_epg
            _original_generate_epg = None
            logger.info("[Timeshift] Restored generate_epg")

        # 5. Restore URLResolver.resolve
        if _original_resolve:
            from django.urls.resolvers import URLResolver
            URLResolver.resolve = _original_resolve
            _original_resolve = None
            logger.info("[Timeshift] Restored URLResolver.resolve")

        logger.info("[Timeshift] All hooks uninstalled successfully")
        return True

    except Exception as e:
        logger.error(f"[Timeshift] Failed to uninstall hooks: {e}", exc_info=True)
        return False


def _patch_xc_get_live_streams():
    """
    Patch xc_get_live_streams to:
    1. Add tv_archive and tv_archive_duration from provider
    2. Replace stream_id with provider's stream_id

    WHY REPLACE stream_id?
        iPlayTV uses stream_id for timeshift URLs. If we keep Dispatcharr's
        internal ID, iPlayTV sends that ID in timeshift requests, and we
        can't find the channel because we search by provider stream_id.

        We also patch stream_xc to handle live streaming with provider IDs.
    """
    global _original_xc_get_live_streams

    from apps.output import views as output_views

    # Guard: check function marker to detect already-patched state.
    # Global check alone is insufficient because uninstall_hooks() resets globals,
    # and Dispatcharr may call install_hooks() multiple times per worker
    # (auto-install + enable action), corrupting _original references.
    if getattr(output_views.xc_get_live_streams, '_is_timeshift_patch', False):
        # Recover native func reference for this worker (inherited via fork)
        if _original_xc_get_live_streams is None:
            _original_xc_get_live_streams = output_views.xc_get_live_streams._native_func
        logger.info("[Timeshift] xc_get_live_streams already patched, skipping")
        return

    _original_xc_get_live_streams = output_views.xc_get_live_streams

    def patched_xc_get_live_streams(request, user, category_id=None):
        streams = _original_xc_get_live_streams(request, user, category_id)

        # Skip if plugin is disabled
        if not _is_plugin_enabled():
            return streams

        from apps.channels.models import Channel

        config = _get_plugin_config()
        debug = config['debug_mode']

        if debug:
            logger.info(f"[Timeshift] API: Processing {len(streams)} streams for timeshift enhancement")

        timeshift_count = 0

        for stream_data in streams:
            original_stream_id = stream_data.get('stream_id')
            try:
                channel = Channel.objects.filter(id=original_stream_id).first()
                if not channel:
                    if debug:
                        logger.info(f"[Timeshift] API: Channel not found for internal_id={original_stream_id}")
                    continue

                # ✅ NEW: Check ALL streams for catch-up support (fallback chain)
                tv_archive = 0
                tv_archive_duration = 0
                catchup_stream = None

                if debug:
                    logger.info(f"[Timeshift] API: Scanning {channel.name} for catch-up support...")

                for stream in channel.streams.order_by('channelstream__order'):
                    stream_props = stream.custom_properties or {}
                    if int(stream_props.get('tv_archive', 0)):
                        tv_archive = 1
                        tv_archive_duration = int(stream_props.get('tv_archive_duration', 0))
                        catchup_stream = stream
                        if debug:
                            logger.info(f"[Timeshift] API:   {stream.name}: tv_archive=1 ✅ (duration={tv_archive_duration}d)")
                        break  # Use first stream with catch-up
                    else:
                        if debug:
                            logger.info(f"[Timeshift] API:   {stream.name}: tv_archive=0")

                # Get first stream for provider_stream_id (API response needs it)
                first_stream = channel.streams.order_by('channelstream__order').first()
                if not first_stream:
                    if debug:
                        logger.info(f"[Timeshift] API: No streams for channel '{channel.name}' (id={original_stream_id})")
                    continue

                props = first_stream.custom_properties or {}

                # Add tv_archive values (based on ANY stream with catch-up)
                stream_data['tv_archive'] = tv_archive
                stream_data['tv_archive_duration'] = tv_archive_duration

                if tv_archive:
                    timeshift_count += 1
                    if debug:
                        logger.info(f"[Timeshift] API: {channel.name} → tv_archive=1 (from {catchup_stream.name if catchup_stream else 'unknown'}), duration={stream_data['tv_archive_duration']}d")

                # Replace stream_id with provider's stream_id (use first stream for consistency)
                provider_stream_id = props.get('stream_id')
                if provider_stream_id:
                    if debug:
                        logger.info(f"[Timeshift] API: {channel.name} → stream_id {original_stream_id} → {provider_stream_id}")
                    stream_data['stream_id'] = int(provider_stream_id)

            except Exception as e:
                logger.error(f"[Timeshift] API: Error enhancing stream internal_id={original_stream_id}: {e}")

        if debug and timeshift_count > 0:
            logger.info(f"[Timeshift] API: Enhanced {timeshift_count}/{len(streams)} channels with timeshift")

        return streams

    patched_xc_get_live_streams._is_timeshift_patch = True
    patched_xc_get_live_streams._native_func = _original_xc_get_live_streams
    output_views.xc_get_live_streams = patched_xc_get_live_streams
    logger.info("[Timeshift] Patched xc_get_live_streams")


def _patch_stream_xc():
    """
    Patch stream_xc to find channels by provider stream_id first.

    WHY THIS PATCH?
        After patching xc_get_live_streams to return provider's stream_id,
        iPlayTV uses that ID in live stream URLs: /live/user/pass/{provider_id}.ts

        But Dispatcharr's stream_xc looks up Channel.objects.get(id=channel_id),
        which fails because the provider ID doesn't match internal IDs.

        This patch first tries to find channel by provider stream_id in
        custom_properties, then falls back to internal ID lookup.

    WHY PATCH URL PATTERNS?
        Simply patching the function in the module doesn't work because Django
        URL patterns keep a reference to the original function from import time.
        We must also update the callback in the urlpatterns list.
    """
    global _original_stream_xc, _original_url_callbacks

    from apps.proxy.ts_proxy import views as proxy_views
    from dispatcharr import urls as main_urls

    if getattr(proxy_views.stream_xc, '_is_timeshift_patch', False):
        if _original_stream_xc is None:
            _original_stream_xc = proxy_views.stream_xc._native_func
        logger.info("[Timeshift] stream_xc already patched, skipping")
        return

    _original_stream_xc = proxy_views.stream_xc

    def patched_stream_xc(request, username, password, channel_id):
        # If plugin is disabled, use original function
        if not _is_plugin_enabled():
            return _original_stream_xc(request, username, password, channel_id)

        import pathlib
        from django.shortcuts import get_object_or_404
        from django.http import JsonResponse
        from apps.accounts.models import User
        from apps.channels.models import Channel, Stream

        config = _get_plugin_config()
        debug = config['debug_mode']

        user = get_object_or_404(User, username=username)

        # Extract channel ID without extension (e.g., "12345.ts" -> "12345")
        channel_id_str = pathlib.Path(channel_id).stem

        if debug:
            logger.info(f"[Timeshift] Live: Request user={username}, channel_id={channel_id_str}")

        custom_properties = user.custom_properties or {}

        if "xc_password" not in custom_properties:
            if debug:
                logger.info(f"[Timeshift] Live: Auth failed - no xc_password for user {username}")
            return JsonResponse({"error": "Invalid credentials"}, status=401)

        if custom_properties["xc_password"] != password:
            if debug:
                logger.info(f"[Timeshift] Live: Auth failed - wrong password for user {username}")
            return JsonResponse({"error": "Invalid credentials"}, status=401)

        channel = None

        # TIMESHIFT FIX: First try to find by provider stream_id
        # This handles the case where API returns provider's stream_id
        stream = Stream.objects.filter(
            custom_properties__stream_id=channel_id_str,
            m3u_account__account_type='XC'
        ).first()
        if stream:
            channel = stream.channels.first()
            if channel and debug:
                logger.info(f"[Timeshift] Live: Found by provider_stream_id={channel_id_str} → {channel.name}")

        # Fall back to original behavior (internal ID lookup)
        if not channel:
            if debug:
                logger.info(f"[Timeshift] Live: Not found by provider_stream_id, trying internal_id")
            try:
                internal_id = int(channel_id_str)
                if user.user_level < 10:
                    user_profile_count = user.channel_profiles.count()

                    if user_profile_count == 0:
                        filters = {
                            "id": internal_id,
                            "user_level__lte": user.user_level
                        }
                        channel = Channel.objects.filter(**filters).first()
                    else:
                        filters = {
                            "id": internal_id,
                            "channelprofilemembership__enabled": True,
                            "user_level__lte": user.user_level,
                            "channelprofilemembership__channel_profile__in": user.channel_profiles.all()
                        }
                        channel = Channel.objects.filter(**filters).distinct().first()
                else:
                    channel = Channel.objects.filter(id=internal_id).first()

                if channel and debug:
                    logger.info(f"[Timeshift] Live: Found by internal_id={internal_id} → {channel.name}")
            except (ValueError, TypeError):
                pass

        if not channel:
            # Error always logged
            logger.error(f"[Timeshift] Live: Channel not found for ID={channel_id_str}")

            # Detailed diagnostics only in debug mode (expensive DB queries)
            if debug:
                diagnostics = []

                # Check if stream exists with this ID but wrong account type
                stream_any_type = Stream.objects.filter(
                    custom_properties__stream_id=channel_id_str
                ).first()
                if stream_any_type:
                    diagnostics.append(
                        f"Stream found but account_type='{stream_any_type.m3u_account.account_type}' (need 'XC')"
                    )
                    if not stream_any_type.channels.exists():
                        diagnostics.append("Stream has no channels assigned")

                # Check total XC streams count (is anything synced?)
                xc_stream_count = Stream.objects.filter(m3u_account__account_type='XC').count()
                diagnostics.append(f"Total XC streams in DB: {xc_stream_count}")

                # Check if internal ID exists but user lacks access
                if channel_id_str.isdigit():
                    ch = Channel.objects.filter(id=int(channel_id_str)).first()
                    if ch:
                        diagnostics.append(
                            f"Channel exists (id={ch.id}, name='{ch.name}') "
                            f"but user_level mismatch (user={user.user_level}, required={ch.user_level})"
                        )

                if diagnostics:
                    logger.info(f"[Timeshift] Live: Diagnostics: {'; '.join(diagnostics)}")

            return JsonResponse({"error": "Not found"}, status=404)

        # Check user access level
        if user.user_level < channel.user_level:
            if debug:
                logger.info(f"[Timeshift] Live: Access denied - user_level {user.user_level} < required {channel.user_level}")
            return JsonResponse({"error": "Not found"}, status=404)

        # Call the original stream_ts function
        from apps.proxy.ts_proxy.views import stream_ts
        # Handle both DRF requests and regular Django requests
        actual_request = getattr(request, '_request', request)
        return stream_ts(actual_request, str(channel.uuid))

    # Patch the module (for any new imports)
    patched_stream_xc._is_timeshift_patch = True
    patched_stream_xc._native_func = _original_stream_xc
    proxy_views.stream_xc = patched_stream_xc

    # CRITICAL: Also patch the URL patterns callbacks
    # Django keeps references to the original function in urlpatterns
    # Store original callbacks so we can restore them later
    for pattern in main_urls.urlpatterns:
        if hasattr(pattern, 'callback') and pattern.callback == _original_stream_xc:
            _original_url_callbacks[id(pattern)] = _original_stream_xc
            pattern.callback = patched_stream_xc
            logger.info(f"[Timeshift] Patched URL pattern: {pattern.name}")

    logger.info("[Timeshift] Patched stream_xc for provider stream_id lookup")


def _patch_xc_get_epg():
    """
    Patch xc_get_epg to find channels by provider stream_id first.

    WHY THIS PATCH?
        After patching xc_get_live_streams to return provider's stream_id,
        IPTV clients use that ID when requesting EPG data via player_api.php
        with action=get_simple_data_table or get_short_epg.

        But Dispatcharr's xc_get_epg looks up Channel.objects.filter(id=stream_id),
        which fails because the provider ID doesn't match internal IDs.

        This patch first tries to find channel by provider stream_id in
        custom_properties, then falls back to internal ID lookup.

    DATA TYPE FIXES FOR SNAPPIER iOS:
        Snappier performs strict JSON validation. We ensure:
        - channel_id: STRING (not int)
        - has_archive: INTEGER 1/0 (not string)
        - start_timestamp/stop_timestamp: STRING (not int)
        - Unique program IDs based on timestamps
    """
    global _original_xc_get_epg

    from apps.output import views as output_views

    def _convert_epg_timestamps_to_local(result, config, debug=False):
        """
        Convert UTC timestamps in XC API EPG response to local timezone.

        WHY: TiviMate displays the 'start' and 'end' string fields verbatim
        without applying timezone conversion. Since Dispatcharr v0.21+ stores
        real UTC in DB (not CET-as-UTC like v0.20.x), we must convert here.

        NOTE: Do NOT also patch xc_get_info timezone - that causes double
        conversion (learned in v1.2.1). Keep server_info.timezone as UTC.
        """
        if not isinstance(result, dict) or 'epg_listings' not in result:
            return result

        from datetime import datetime, timezone as dt_timezone
        from zoneinfo import ZoneInfo

        timezone_str = config['timezone']
        local_tz = ZoneInfo(timezone_str)
        # Use datetime.timezone.utc instead of ZoneInfo("UTC") because
        # ZoneInfo("UTC") can return wrong offset when /etc/timezone differs
        # from /etc/localtime in Docker containers (v1.2.4 fix).
        utc_tz = dt_timezone.utc
        converted = 0

        for listing in result['epg_listings']:
            for field in ('start', 'end'):
                val = listing.get(field)
                if not val:
                    continue
                try:
                    dt = datetime.strptime(val, "%Y-%m-%d %H:%M:%S")
                    dt = dt.replace(tzinfo=utc_tz)
                    listing[field] = dt.astimezone(local_tz).strftime("%Y-%m-%d %H:%M:%S")
                    converted += 1
                except (ValueError, TypeError):
                    pass

        if debug and converted:
            logger.info(f"[Timeshift] EPG: Converted {converted} timestamps to {timezone_str}")

        return result

    if getattr(output_views.xc_get_epg, '_is_timeshift_patch', False):
        if _original_xc_get_epg is None:
            _original_xc_get_epg = output_views.xc_get_epg._native_func
        logger.info("[Timeshift] xc_get_epg already patched, skipping")
        return

    _original_xc_get_epg = output_views.xc_get_epg

    def patched_xc_get_epg(request, user, short=False):
        # If plugin is disabled, use original function
        if not _is_plugin_enabled():
            return _original_xc_get_epg(request, user, short)

        from django.http import Http404
        from apps.channels.models import Channel, Stream

        config = _get_plugin_config()
        debug = config['debug_mode']

        channel_id = request.GET.get('stream_id')
        if not channel_id:
            logger.error("[Timeshift] EPG: Request missing stream_id parameter")
            raise Http404()

        if debug:
            logger.info(f"[Timeshift] EPG: Request stream_id={channel_id}, short={short}")

        channel = None

        try:
            # TIMESHIFT FIX: First try to find by provider stream_id
            # This handles the case where API returns provider's stream_id
            stream = Stream.objects.filter(
                custom_properties__stream_id=str(channel_id),
                m3u_account__account_type='XC'
            ).first()
            if stream:
                channel = stream.channels.first()
                if channel and debug:
                    logger.info(f"[Timeshift] EPG: Found by provider_stream_id={channel_id} → {channel.name}")

            # Fall back to original behavior (internal ID lookup)
            if not channel:
                if debug:
                    logger.info(f"[Timeshift] EPG: Not found by provider_stream_id, trying internal_id")
                if user.user_level < 10:
                    user_profile_count = user.channel_profiles.count()

                    if user_profile_count == 0:
                        channel = Channel.objects.filter(
                            id=channel_id,
                            user_level__lte=user.user_level
                        ).first()
                    else:
                        filters = {
                            "id": channel_id,
                            "channelprofilemembership__enabled": True,
                            "user_level__lte": user.user_level,
                            "channelprofilemembership__channel_profile__in": user.channel_profiles.all()
                        }
                        channel = Channel.objects.filter(**filters).distinct().first()
                else:
                    channel = Channel.objects.filter(id=channel_id).first()

                if channel and debug:
                    logger.info(f"[Timeshift] EPG: Found by internal_id={channel_id} → {channel.name}")

            if not channel:
                logger.error(f"[Timeshift] EPG: Channel not found for stream_id={channel_id}")
                raise Http404()

            # ✅ NEW: Find first stream in channel with catch-up support
            catchup_stream = None
            has_tv_archive = False

            if debug:
                logger.info(f"[Timeshift] EPG: Scanning {channel.name} for catch-up stream...")

            for stream in channel.streams.order_by('channelstream__order'):
                stream_props = stream.custom_properties or {}
                if int(stream_props.get('tv_archive', 0)):
                    catchup_stream = stream
                    has_tv_archive = True
                    if debug:
                        logger.info(f"[Timeshift] EPG: Using catch-up stream: {stream.name}")
                    break
                else:
                    if debug:
                        logger.info(f"[Timeshift] EPG:   {stream.name}: no catch-up")

            # Use first stream if no catch-up stream found (for consistency)
            if not catchup_stream:
                catchup_stream = channel.streams.order_by('channelstream__order').first()

            props = catchup_stream.custom_properties or {} if catchup_stream else {}

            if debug:
                logger.info(f"[Timeshift] EPG: {channel.name} tv_archive={has_tv_archive}, stream={catchup_stream.name if catchup_stream else 'none'}")

            if has_tv_archive and not short:
                # CUSTOM EPG RESPONSE: Include past programs for timeshift
                from datetime import timedelta
                from django.utils import timezone as django_timezone
                from zoneinfo import ZoneInfo
                import base64

                # Get plugin config
                timezone_str = config['timezone']
                language = config['language']
                local_tz = ZoneInfo(timezone_str)

                archive_duration_days = int(props.get('tv_archive_duration', 7))
                start_date = django_timezone.now() - timedelta(days=archive_duration_days)

                if debug:
                    logger.info(f"[Timeshift] EPG: Generating custom EPG for {channel.name}, archive={archive_duration_days}d, tz={timezone_str}")

                # Get programs from the last X days until future
                programs = channel.epg_data.programs.filter(
                    start_time__gte=start_date
                ).order_by('start_time') if channel.epg_data else []

                output = {"epg_listings": []}
                now = django_timezone.now()
                archive_count = 0

                for program in programs:
                    start = program.start_time
                    end = program.end_time
                    title = program.title or ""
                    description = program.description or ""

                    # Convert timestamps to local timezone for XC API clients
                    # WHY: TiviMate displays start/end strings verbatim without
                    # timezone conversion. Container runs in UTC, so we convert here.
                    # DO NOT also patch xc_get_info timezone - that causes double conversion.
                    # Verified: matches v0.17 behavior that worked correctly.
                    start_local = start.astimezone(local_tz)
                    end_local = end.astimezone(local_tz)

                    # Generate unique ID for each program using timestamp
                    program_id = int(start.timestamp())

                    program_output = {
                        "id": str(program_id),  # STRING - unique per program
                        "epg_id": str(program.id) if hasattr(program, 'id') and program.id else str(program_id),
                        "title": base64.b64encode(title.encode()).decode(),
                        "lang": language,  # From plugin settings
                        "start": start_local.strftime("%Y-%m-%d %H:%M:%S"),  # Local time
                        "end": end_local.strftime("%Y-%m-%d %H:%M:%S"),      # Local time
                        "description": base64.b64encode(description.encode()).decode(),
                        "channel_id": str(props.get('epg_channel_id') or channel.id),  # STRING
                        "start_timestamp": str(int(start.timestamp())),  # STRING not int (UTC epoch)
                        "stop_timestamp": str(int(end.timestamp())),     # STRING not int (UTC epoch)
                        "stream_id": str(props.get('stream_id', channel.id)),  # Provider's stream_id
                        "now_playing": 0 if start > now or end < now else 1,
                        "has_archive": 0,  # INTEGER not string - default
                    }

                    # Set has_archive for past programs within archive duration
                    if end < now:
                        days_ago = (now - end).days
                        if days_ago <= archive_duration_days:
                            program_output["has_archive"] = 1  # INTEGER
                            archive_count += 1

                    output['epg_listings'].append(program_output)

                if debug:
                    logger.info(f"[Timeshift] EPG: Generated {len(output['epg_listings'])} programs ({archive_count} with archive) for {channel.name}")

                return output
            else:
                # No timeshift or short=True - delegate to original,
                # then convert UTC timestamps to local timezone.
                # WHY: Dispatcharr v0.21+ stores real UTC in DB. TiviMate
                # displays start/end strings verbatim → shows -1h without
                # conversion. DO NOT patch xc_get_info (double conversion
                # bug from v1.2.1).
                if debug:
                    logger.info(f"[Timeshift] EPG: Delegating to original (tv_archive={has_tv_archive}, short={short})")

                from django.http import QueryDict
                original_get = request.GET

                # Create a mutable copy and update stream_id to internal ID
                new_get = original_get.copy()
                new_get['stream_id'] = str(channel.id)
                request.GET = new_get

                try:
                    result = _original_xc_get_epg(request, user, short)
                    result = _convert_epg_timestamps_to_local(result, config, debug)
                    return result
                finally:
                    request.GET = original_get

        except Http404:
            raise
        except Exception as e:
            logger.error(f"[Timeshift] EPG: Unexpected error for stream_id={channel_id}: {e}", exc_info=True)
            raise Http404()

    patched_xc_get_epg._is_timeshift_patch = True
    patched_xc_get_epg._native_func = _original_xc_get_epg
    output_views.xc_get_epg = patched_xc_get_epg
    logger.info("[Timeshift] Patched xc_get_epg for provider stream_id lookup")


def _patch_generate_epg():
    """
    Patch generate_epg to convert XMLTV timestamps to local timezone.

    WHY THIS PATCH?
        IPTVX and other IPTV clients fetch EPG data via /output/epg endpoint
        which returns XMLTV format. The timestamps are stored in UTC but
        IPTVX displays them as-is without timezone conversion.

        This patch wraps the generate_epg generator to convert timestamps
        from UTC to the configured local timezone.
    """
    global _original_generate_epg

    from apps.output import views as output_views

    if getattr(output_views.generate_epg, '_is_timeshift_patch', False):
        if _original_generate_epg is None:
            _original_generate_epg = output_views.generate_epg._native_func
        logger.info("[Timeshift] generate_epg already patched, skipping")
        return

    _original_generate_epg = output_views.generate_epg

    def patched_generate_epg(request, profile_name=None, user=None):
        # If plugin is disabled, use original function
        if not _is_plugin_enabled():
            return _original_generate_epg(request, profile_name, user)

        try:
            from zoneinfo import ZoneInfo
            from django.http import StreamingHttpResponse
            import re

            # Get timezone from plugin settings
            plugin_config = _get_plugin_config()
            timezone_str = plugin_config['timezone']
            debug = plugin_config['debug_mode']
            xmltv_override = plugin_config['xmltv_prev_days_override']
            local_tz = ZoneInfo(timezone_str)

            if debug:
                logger.info(f"[Timeshift] XMLTV: Converting timestamps to {timezone_str}")

            # Inject prev_days so XMLTV includes past programmes for catch-up
            # clients (TiviMate, IPTVnator). Dispatcharr core defaults prev_days
            # to 0 → no past data → no has_archive icons after a client cache
            # clear. Source of truth: provider's tv_archive_duration on each
            # XC stream (filled at M3U sync). We take the max across streams
            # with tv_archive=1 so every channel's catch-up window is covered.
            # Plugin setting xmltv_prev_days_override (>0) bypasses the
            # auto-detection. URL ?prev_days= and user.epg_prev_days keep
            # precedence so admins can still override per-request/per-user.
            user_prev = (user.custom_properties or {}).get('epg_prev_days') if user else None
            url_has_prev = 'prev_days' in request.GET
            inject_value = 0
            if not url_has_prev and not user_prev:
                if xmltv_override > 0:
                    inject_value = xmltv_override
                else:
                    inject_value = _compute_provider_archive_days()
            should_inject = inject_value > 0
            original_get = request.GET
            if should_inject:
                new_get = original_get.copy()
                new_get['prev_days'] = str(inject_value)
                request.GET = new_get
                if debug:
                    src = 'override' if xmltv_override > 0 else 'provider'
                    logger.info(f"[Timeshift] XMLTV: Injecting prev_days={inject_value} (source: {src})")

            try:
                # Call original function to get response
                original_response = _original_generate_epg(request, profile_name, user)
            finally:
                if should_inject:
                    request.GET = original_get

            # Pattern to match XMLTV timestamps: 20251128143000 +0000
            timestamp_pattern = re.compile(r'(\d{14}) ([+-]\d{4})')

            # Handle both StreamingHttpResponse and regular HttpResponse
            if hasattr(original_response, 'streaming_content'):
                # StreamingHttpResponse - use generator
                original_generator = original_response.streaming_content
            else:
                # Regular HttpResponse - convert content to single-item generator
                content = original_response.content
                if isinstance(content, bytes):
                    content = content.decode('utf-8')
                original_generator = iter([content])

            def timezone_converting_generator():
                from datetime import datetime

                for chunk in original_generator:
                    # Ensure chunk is string
                    if isinstance(chunk, bytes):
                        chunk = chunk.decode('utf-8')

                    # Only process chunks with programme timestamps
                    if 'start="' in chunk or 'stop="' in chunk:
                        def convert_timestamp(match):
                            timestamp_str = match.group(1)
                            try:
                                utc_time = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
                                # Use datetime.timezone.utc instead of ZoneInfo("UTC")
                                # because ZoneInfo("UTC") returns wrong offset when
                                # /etc/timezone conflicts with /etc/localtime (Docker).
                                from datetime import timezone as dt_timezone
                                utc_time = utc_time.replace(tzinfo=dt_timezone.utc)
                                local_time = utc_time.astimezone(local_tz)
                                return local_time.strftime("%Y%m%d%H%M%S %z")
                            except Exception as e:
                                if debug:
                                    logger.info(f"[Timeshift] XMLTV: Timestamp conversion failed for '{timestamp_str}': {e}")
                                return match.group(0)

                        chunk = timestamp_pattern.sub(convert_timestamp, chunk)

                    yield chunk

            # Return new StreamingHttpResponse with converted timestamps
            response = StreamingHttpResponse(
                timezone_converting_generator(),
                content_type='application/xml'
            )
            response['Content-Disposition'] = 'attachment; filename="Dispatcharr.xml"'
            response['Cache-Control'] = 'no-cache'
            return response

        except Exception as e:
            logger.error(f"[Timeshift] XMLTV: Generation error, falling back to original: {e}")
            return _original_generate_epg(request, profile_name, user)

    patched_generate_epg._is_timeshift_patch = True
    patched_generate_epg._native_func = _original_generate_epg
    output_views.generate_epg = patched_generate_epg
    logger.info("[Timeshift] Patched generate_epg for XMLTV timezone conversion")


def _patch_url_resolver():
    """
    Patch URLResolver.resolve to intercept /timeshift/ URLs.

    WHY THIS APPROACH:
        Dispatcharr's urls.py has a catch-all pattern at the end:
            path("<path:unused_path>", views.handle_404)

        This catches ALL unmatched URLs, including our /timeshift/ URLs.
        By patching URLResolver.resolve(), we intercept the URL BEFORE
        any pattern matching happens.

    URL FORMAT FROM iPlayTV:
        /timeshift/{user}/{pass}/{epg_channel}/{timestamp}/{provider_stream_id}.ts

        QUIRK: iPlayTV sends parameters in unexpected positions:
        - Position 3 (stream_id param) = EPG channel number (NOT used)
        - Position 5 (duration param) = Provider's stream_id (USED for lookup)

    IDEMPOTENCY (issue #14):
        Without a function-marker guard, calling _patch_url_resolver() twice
        captures the already-patched resolve as "original", causing infinite
        recursion on subsequent re-discovers (force_reload=True). The other
        4 patches use _is_timeshift_patch on the function; we use the same
        pattern here.
    """
    global _original_resolve

    from django.urls.resolvers import URLResolver

    # Idempotent guard: detect if URLResolver.resolve is already our patch.
    # The other guards in this module use the same _is_timeshift_patch marker.
    current = URLResolver.resolve
    if getattr(current, '_is_timeshift_patch', False):
        if _original_resolve is None:
            _original_resolve = current._native_func
        logger.info("[Timeshift] URLResolver already patched, skipping")
        return

    from .views import timeshift_proxy

    TIMESHIFT_PATTERN = re.compile(
        r'^/?timeshift/(?P<username>[^/]+)/(?P<password>[^/]+)/'
        r'(?P<stream_id>\d+)/(?P<timestamp>[\d\-:]+)/(?P<duration>\d+)\.ts$'
    )

    _original_resolve = current

    def patched_resolve(self, path):
        # Only intercept if plugin is enabled
        if _is_plugin_enabled() and (path.startswith('/timeshift/') or path.startswith('timeshift/')):
            match = TIMESHIFT_PATTERN.match(path)
            if match:
                from django.urls import ResolverMatch
                config = _get_plugin_config()
                if config['debug_mode']:
                    logger.info(f"[Timeshift] URL intercepted: {path}")
                return ResolverMatch(
                    timeshift_proxy,
                    (),
                    match.groupdict(),
                    route=path,
                )
        return _original_resolve(self, path)

    patched_resolve._is_timeshift_patch = True
    patched_resolve._native_func = _original_resolve
    URLResolver.resolve = patched_resolve
    logger.info("[Timeshift] Patched URLResolver.resolve")
