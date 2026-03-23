"""
Dispatcharr Timeshift Plugin - Views

Handles /timeshift/ requests by proxying to the Xtream Codes provider.

URL FORMAT FROM iPlayTV:
    /timeshift/{username}/{password}/{epg_channel}/{timestamp}/{provider_stream_id}.ts

    Example: /timeshift/john/secret123/155/2025-01-15:14-30/22371.ts

    QUIRK - Parameter positions are misleading:
        The URL pattern names don't match their actual meaning:
        - Position 3 (stream_id param) = EPG channel number (NOT used for lookup)
        - Position 5 (duration param) = Provider's stream_id (USED for lookup)

        This is how iPlayTV constructs timeshift URLs. We can't change it,
        so we work around it by ignoring position 3 and using position 5.

TIMESTAMP HANDLING:
    The timestamp (e.g., "2025-01-15:14-30") is converted from UTC to the
    provider's local timezone before being sent. IPTV clients (iPlayTV, TiviMate,
    Televizo) use the start_timestamp field from EPG (which is UTC unix timestamp)
    to construct timeshift URLs. XC providers expect local time, so we convert
    UTC -> Local. The timezone is configurable in plugin settings (defaults to
    Europe/Brussels).

AUTHENTICATION:
    Uses Dispatcharr's xc_password (stored in user.custom_properties),
    NOT the regular Django password. This matches how other XC endpoints work.

GitHub: https://github.com/cedric-marcoux/dispatcharr_timeshift
"""

import logging
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from django.http import StreamingHttpResponse, Http404, HttpResponseBadRequest, HttpResponseForbidden

logger = logging.getLogger("plugins.dispatcharr_timeshift.views")

# Cache for URL format preferences per m3u_account
# Key: m3u_account.id, Value: 'A' (query string) or 'B' (path-based)
# This is session-scoped (cleared on restart) - no persistence needed
_url_format_cache = {}


def _get_programme_duration(channel, timestamp_str):
    """
    Get programme duration from EPG based on timestamp.

    iPlayTV sends the start time of the programme the user wants to watch.
    We look up that programme in EPG and calculate its actual duration.

    Args:
        channel: Channel object with epg_data relation
        timestamp_str: Timestamp in format YYYY-MM-DD:HH-MM (provider's local time)

    Returns:
        int: Duration in minutes, or 120 as fallback
    """
    DEFAULT_DURATION = 120  # 2 hours fallback
    BUFFER_MINUTES = 5      # Add buffer for stream startup

    try:
        # Parse timestamp (already converted to provider's local timezone)
        # Make timezone-aware so Django doesn't warn about naive datetimes
        from datetime import timezone as dt_timezone
        dt = datetime.strptime(timestamp_str, "%Y-%m-%d:%H-%M").replace(tzinfo=dt_timezone.utc)

        # Check if channel has EPG data
        if not channel.epg_data:
            return DEFAULT_DURATION

        # Find programme that contains this timestamp
        # The timestamp is the programme start time from iPlayTV
        programme = channel.epg_data.programs.filter(
            start_time__lte=dt,
            end_time__gt=dt
        ).first()

        if not programme:
            return DEFAULT_DURATION

        # Calculate duration from programme
        duration_seconds = (programme.end_time - programme.start_time).total_seconds()
        duration_minutes = int(duration_seconds / 60) + BUFFER_MINUTES

        # Cap at reasonable maximum (8 hours) to avoid issues
        duration_minutes = min(duration_minutes, 480)

        return duration_minutes

    except Exception:
        return DEFAULT_DURATION


def _build_timeshift_url_format_a(m3u_account, stream_id, timestamp, duration_minutes):
    """Build timeshift URL using query string format (streaming/timeshift.php)."""
    return (
        f"{m3u_account.server_url.rstrip('/')}/streaming/timeshift.php"
        f"?username={m3u_account.username}"
        f"&password={m3u_account.password}"
        f"&stream={stream_id}"
        f"&start={timestamp}"
        f"&duration={duration_minutes}"
    )


def _build_timeshift_url_format_b(m3u_account, stream_id, timestamp, duration_minutes):
    """Build timeshift URL using path format (/timeshift/user/pass/duration/time/id.ts)."""
    return (
        f"{m3u_account.server_url.rstrip('/')}/timeshift"
        f"/{m3u_account.username}"
        f"/{m3u_account.password}"
        f"/{duration_minutes}"
        f"/{timestamp}"
        f"/{stream_id}.ts"
    )


def timeshift_proxy(request, username, password, stream_id, timestamp, duration):
    """
    Proxy timeshift request to Xtream Codes provider.

    Args:
        username: Dispatcharr username
        password: Dispatcharr user's xc_password (NOT Django password)
        stream_id: EPG channel number - IGNORED (see module docstring)
        timestamp: Start time as YYYY-MM-DD:HH-MM (passed to provider as-is)
        duration: Provider's stream_id - ACTUALLY USED (misleading param name)

    Returns:
        StreamingHttpResponse proxying the video stream from provider
    """
    from .hooks import _get_plugin_config

    # QUIRK: The "duration" param is actually the provider's stream_id
    # See module docstring for explanation of iPlayTV's URL format
    provider_stream_id = duration.rstrip('.ts')

    # Load plugin config
    config = _get_plugin_config()
    debug = config['debug_mode']
    url_format = config['url_format']
    custom_template = config['custom_url_template']
    timezone_str = config['timezone']

    if debug:
        logger.info(f"[Timeshift] === REQUEST START ===")
        logger.info(f"[Timeshift] User: {username}, Provider stream_id: {provider_stream_id}")
        logger.info(f"[Timeshift] Timestamp (raw): {timestamp}, URL stream_id: {stream_id}")
        logger.info(f"[Timeshift] Config: url_format={url_format}, timezone={timezone_str}, debug={debug}")

    # Step 1: Authenticate user via xc_password
    user = _authenticate_user(username, password)
    if not user:
        return HttpResponseForbidden("Invalid credentials")

    # Step 2: Find channel by provider's stream_id
    # We search custom_properties.stream_id, NOT Dispatcharr's internal ID
    channel, stream = _find_channel_by_provider_stream_id(provider_stream_id)
    if not channel:
        raise Http404("Channel not found")

    if debug:
        logger.info(f"[Timeshift] Channel found: {channel.name} (id={channel.id})")

    # Step 3: Verify user has access to this channel
    if user.user_level < channel.user_level:
        logger.error(f"[Timeshift] Access denied: user {username} (level {user.user_level}) < channel {channel.name} (level {channel.user_level})")
        return HttpResponseForbidden("Access denied")

    # Step 4: Verify channel supports timeshift
    props = stream.custom_properties or {}
    if props.get('tv_archive') not in (1, '1'):
        logger.error(f"[Timeshift] Channel {channel.name} does not support timeshift (tv_archive={props.get('tv_archive')})")
        return HttpResponseBadRequest("Timeshift not supported for this channel")

    if debug:
        logger.info(f"[Timeshift] Stream props: {props}")

    # Step 5: Verify it's an Xtream Codes provider
    m3u_account = stream.m3u_account
    if not m3u_account or m3u_account.account_type != 'XC':
        return HttpResponseBadRequest("Channel not from Xtream Codes provider")

    # Step 6: Convert timestamp from UTC to provider's local timezone
    local_timestamp = _convert_timestamp_to_local(timestamp, timezone_str)
    if debug:
        logger.info(f"[Timeshift] Timestamp: {timestamp} (UTC) → {local_timestamp} ({timezone_str})")

    # Step 6.5: Get programme duration from EPG
    duration_minutes = _get_programme_duration(channel, local_timestamp)
    if debug:
        logger.info(f"[Timeshift] Duration from EPG: {duration_minutes} minutes")

    # Step 7: Build provider's timeshift URL based on configured format
    stream_id_value = props.get('stream_id')
    timeshift_url = None
    fallback_url = None

    if url_format == 'custom' and custom_template:
        # Custom template - provide all available variables
        try:
            # Parse local timestamp to Unix epoch for providers that use it
            local_dt = datetime.strptime(local_timestamp, "%Y-%m-%d:%H-%M")
            local_dt = local_dt.replace(tzinfo=ZoneInfo(timezone_str))
            start_unix = int(local_dt.timestamp())
        except Exception:
            start_unix = 0

        timeshift_url = custom_template.format(
            server_url=m3u_account.server_url.rstrip('/'),
            username=m3u_account.username,
            password=m3u_account.password,
            stream_id=stream_id_value,
            timestamp=local_timestamp,
            duration=duration_minutes,
            start_unix=start_unix,
            epg_channel_id=props.get('epg_channel_id', ''),
            channel_name=channel.name,
            channel_id=channel.id,
            tv_archive_duration=props.get('tv_archive_duration', 7),
            extension=props.get('container_extension', 'ts'),
        )
        if debug:
            logger.info(f"[Timeshift] Using custom template")
    elif url_format == 'format_a':
        # Force Format A only
        timeshift_url = _build_timeshift_url_format_a(m3u_account, stream_id_value, local_timestamp, duration_minutes)
        if debug:
            logger.info(f"[Timeshift] Using Format A (forced)")
    elif url_format == 'format_b':
        # Force Format B only
        timeshift_url = _build_timeshift_url_format_b(m3u_account, stream_id_value, local_timestamp, duration_minutes)
        if debug:
            logger.info(f"[Timeshift] Using Format B (forced)")
    else:
        # Auto-detect (default): Try A first, fallback to B
        preferred_format = _url_format_cache.get(m3u_account.id, 'A')
        if preferred_format == 'B':
            timeshift_url = _build_timeshift_url_format_b(m3u_account, stream_id_value, local_timestamp, duration_minutes)
            if debug:
                logger.info(f"[Timeshift] Using Format B (cached for account {m3u_account.id})")
        else:
            timeshift_url = _build_timeshift_url_format_a(m3u_account, stream_id_value, local_timestamp, duration_minutes)
            fallback_url = _build_timeshift_url_format_b(m3u_account, stream_id_value, local_timestamp, duration_minutes)
            if debug:
                logger.info(f"[Timeshift] Using Format A with B fallback (auto-detect)")

    if debug:
        # Log URL without credentials for security
        url_safe = timeshift_url.split('?')[0] if '?' in timeshift_url else timeshift_url.rsplit('/', 3)[0] + '/...'
        logger.info(f"[Timeshift] Built URL: {url_safe}")

    # Minimal log in normal mode - just channel name and timestamp
    if not debug:
        logger.info(f"[Timeshift] {channel.name} @ {local_timestamp}")

    # Step 8: Get User-Agent from M3U account settings
    user_agent = m3u_account.get_user_agent().user_agent

    # Step 9: Proxy the stream (with fallback support)
    return _proxy_stream(request, timeshift_url, user_agent, fallback_url, m3u_account.id, debug)


def _authenticate_user(username, password):
    """
    Authenticate user by username and xc_password.

    Dispatcharr stores XC credentials in user.custom_properties.xc_password,
    separate from the Django auth password. This allows different passwords
    for web UI vs IPTV clients.

    Returns:
        User object if authenticated, None otherwise
    """
    from apps.accounts.models import User

    try:
        user = User.objects.get(username=username)
        xc_password = (user.custom_properties or {}).get('xc_password')
        if not xc_password:
            logger.error(f"[Timeshift] Auth failed: user '{username}' has no xc_password")
            return None
        if xc_password != password:
            logger.error(f"[Timeshift] Auth failed: wrong password for user '{username}'")
            return None
        return user
    except User.DoesNotExist:
        logger.error(f"[Timeshift] Auth failed: user '{username}' does not exist")
        return None


def _find_channel_by_provider_stream_id(provider_stream_id):
    """
    Find channel by the provider's stream_id stored in custom_properties,
    preferring a stream with tv_archive=1 for timeshift support.

    The provider_stream_id (e.g., 22371) comes from the XC provider's API
    and is stored in stream.custom_properties.stream_id during M3U sync.
    This is different from Dispatcharr's internal channel ID.

    Strategy:
      1. Find all XC streams whose custom_properties.stream_id matches.
      2. Resolve a channel from those streams.
      3. Scan ALL streams on that channel (ordered by priority) looking for
         one with tv_archive=1.  Fall back to the originally-matched stream
         if none is found.

    Returns:
        Tuple of (Channel, Stream) if found, (None, None) otherwise
    """
    from apps.channels.models import Stream

    # Search for all streams where custom_properties.stream_id matches
    # Only look at XC provider streams
    matched_streams = Stream.objects.filter(
        custom_properties__stream_id=str(provider_stream_id),
        m3u_account__account_type='XC'
    )

    logger.info(f"[Timeshift] Lookup provider_stream_id={provider_stream_id}: "
                f"found {matched_streams.count()} matching stream(s)")

    for s in matched_streams:
        props = s.custom_properties or {}
        logger.info(f"[Timeshift]   Matched stream: id={s.id} name='{s.name}' "
                    f"tv_archive={props.get('tv_archive')} account='{s.m3u_account}'")

    # Use the last matched stream as the anchor to resolve the channel
    anchor_stream = matched_streams.last()
    if not anchor_stream:
        logger.error(f"[Timeshift] Channel not found: provider_stream_id={provider_stream_id}")
        return None, None

    channel = anchor_stream.channels.last()
    if not channel:
        logger.error(f"[Timeshift] Stream found but no channel for provider_stream_id={provider_stream_id}")
        return None, None

    logger.info(f"[Timeshift] Resolved channel: id={channel.id} name='{channel.name}'")

    # Scan all streams on this channel to find one with tv_archive=1
    all_channel_streams = channel.streams.filter(
        m3u_account__account_type='XC'
    ).order_by('channelstream__order')

    logger.info(f"[Timeshift] Scanning {all_channel_streams.count()} XC stream(s) on channel '{channel.name}' for tv_archive support:")

    catchup_stream = None
    for s in all_channel_streams:
        props = s.custom_properties or {}
        tv_archive = props.get('tv_archive')
        logger.info(f"[Timeshift]   stream id={s.id} name='{s.name}' "
                    f"stream_id={props.get('stream_id')} tv_archive={tv_archive}")
        if tv_archive in (1, '1'):
            catchup_stream = s
            logger.info(f"[Timeshift]   ^ Selected as catchup stream (first with tv_archive=1)")
            break

    if catchup_stream:
        logger.info(f"[Timeshift] Using catchup stream: id={catchup_stream.id} name='{catchup_stream.name}'")
        return channel, catchup_stream

    # No tv_archive=1 stream found — fall back to anchor so the caller
    # can return a meaningful error rather than a silent 404
    logger.warning(f"[Timeshift] No tv_archive=1 stream found on channel '{channel.name}', "
                   f"falling back to anchor stream id={anchor_stream.id} name='{anchor_stream.name}'")
    return channel, anchor_stream


def _proxy_stream(request, url, user_agent, fallback_url=None, m3u_account_id=None, debug=False):
    """
    Proxy video stream from provider to client with fallback support.

    Supports HTTP Range requests for seek/forward/rewind functionality.
    iPlayTV sends Range headers when user seeks in the timeline.

    If primary URL returns 400 and fallback_url is provided, tries the fallback.
    On success, caches the working format for the m3u_account.

    Args:
        request: Django request object
        url: Provider's timeshift URL (primary)
        user_agent: User-Agent string from M3U account settings
        fallback_url: Alternative URL format to try if primary returns 400
        m3u_account_id: M3U account ID for caching format preference
        debug: Enable verbose logging

    Returns:
        StreamingHttpResponse with video content (status 200 or 206)
    """
    headers = {
        'User-Agent': user_agent
    }

    # Forward Range header for seek support
    # Without this, seeking in iPlayTV would fail
    range_header = request.META.get('HTTP_RANGE')
    if range_header:
        headers['Range'] = range_header
        if debug:
            logger.info(f"[Timeshift] Forwarding Range header: {range_header}")

    if debug:
        logger.info(f"[Timeshift] Request headers: {headers}")

    try:
        response = requests.get(url, headers=headers, stream=True, timeout=10)

        if debug:
            logger.info(f"[Timeshift] Provider response: status={response.status_code}")

        # If 400 error and we have a fallback URL, try the alternative format
        if response.status_code == 400 and fallback_url:
            if debug:
                logger.info(f"[Timeshift] Format A returned 400, trying Format B...")
            response.close()

            response = requests.get(fallback_url, headers=headers, stream=True, timeout=10)

            if debug:
                logger.info(f"[Timeshift] Fallback response: status={response.status_code}")

            # If fallback works, cache the format preference
            if response.status_code in (200, 206) and m3u_account_id:
                _url_format_cache[m3u_account_id] = 'B'
                if debug:
                    logger.info(f"[Timeshift] Format B works, cached for account {m3u_account_id}")

        # 200 = full content, 206 = partial content (Range request)
        if response.status_code not in (200, 206):
            # Try to get response body for diagnostics
            try:
                body_preview = response.text[:200] if response.text else 'empty'
            except Exception:
                body_preview = 'unreadable'
            logger.error(f"[Timeshift] Provider error: status={response.status_code}, "
                        f"content-type={response.headers.get('Content-Type', 'unknown')}, "
                        f"body={body_preview}")
            return HttpResponseBadRequest(f"Provider error: {response.status_code}")

        def stream_generator():
            for chunk in response.iter_content(chunk_size=8192):
                yield chunk

        streaming_response = StreamingHttpResponse(
            stream_generator(),
            content_type=response.headers.get('Content-Type', 'video/mp2t'),
            status=response.status_code
        )

        # Copy headers needed for seek support
        # Content-Range tells client which bytes are being sent
        # Accept-Ranges tells client that seeking is supported
        for header in ['Content-Length', 'Content-Range', 'Accept-Ranges']:
            if header in response.headers:
                streaming_response[header] = response.headers[header]

        if debug:
            logger.info(f"[Timeshift] Streaming started: status={response.status_code}, "
                       f"content-type={response.headers.get('Content-Type', 'unknown')}")
            logger.info(f"[Timeshift] === REQUEST END ===")

        return streaming_response

    except requests.exceptions.Timeout:
        logger.error(f"[Timeshift] Provider timeout after 10s")
        return HttpResponseBadRequest("Provider timeout")
    except requests.exceptions.ConnectionError as e:
        logger.error(f"[Timeshift] Provider connection error: {e}")
        return HttpResponseBadRequest("Provider connection error")
    except requests.exceptions.RequestException as e:
        logger.error(f"[Timeshift] Provider request error: {e}")
        return HttpResponseBadRequest("Provider connection error")


def _get_plugin_timezone():
    """
    Get configured timezone from plugin settings.

    Returns:
        str: Timezone string (e.g., "Europe/Brussels"), defaults to "Europe/Brussels"
    """
    try:
        from apps.plugins.models import PluginConfig
        config = PluginConfig.objects.filter(key='dispatcharr_timeshift').first()
        if config and config.settings:
            return config.settings.get('timezone', 'Europe/Brussels')
    except Exception as e:
        logger.debug(f"[Timeshift] Could not load timezone setting: {e}")
    return "Europe/Brussels"


def _convert_timestamp_to_local(timestamp, timezone_str):
    """
    Convert UTC timestamp to local timezone for provider.

    iPlayTV sends timestamps in UTC (from EPG), but XC providers typically
    expect timestamps in local time. This function converts accordingly.

    Args:
        timestamp: UTC timestamp in format YYYY-MM-DD:HH-MM
        timezone_str: Target timezone (IANA format, e.g., "Europe/Brussels")

    Returns:
        str: Converted timestamp in same format, or original if conversion fails
    """
    try:
        # Parse: YYYY-MM-DD:HH-MM
        utc_time = datetime.strptime(timestamp, "%Y-%m-%d:%H-%M")
        # Use datetime.timezone.utc instead of ZoneInfo("UTC") because
        # ZoneInfo("UTC") can return wrong offset when /etc/timezone differs
        # from /etc/localtime (common in Docker containers).
        # See: Python reads /etc/timezone → CET, corrupting ZoneInfo("UTC").
        from datetime import timezone as dt_timezone
        utc_time = utc_time.replace(tzinfo=dt_timezone.utc)

        # Convert to target timezone
        local_time = utc_time.astimezone(ZoneInfo(timezone_str))
        return local_time.strftime("%Y-%m-%d:%H-%M")
    except Exception as e:
        logger.error(f"[Timeshift] Timestamp conversion failed for '{timestamp}': {e}")
        return timestamp
