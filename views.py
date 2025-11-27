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
    provider's local timezone before being sent. iPlayTV sends UTC timestamps
    from EPG data, but XC providers expect local time. The timezone is
    configurable in plugin settings (defaults to Europe/Brussels).

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
    # QUIRK: The "duration" param is actually the provider's stream_id
    # See module docstring for explanation of iPlayTV's URL format
    provider_stream_id = duration.rstrip('.ts')

    logger.info(f"[Timeshift] Request: provider_stream_id={provider_stream_id}, timestamp={timestamp}")

    # Step 1: Authenticate user via xc_password
    user = _authenticate_user(username, password)
    if not user:
        return HttpResponseForbidden("Invalid credentials")

    # Step 2: Find channel by provider's stream_id
    # We search custom_properties.stream_id, NOT Dispatcharr's internal ID
    channel, stream = _find_channel_by_provider_stream_id(provider_stream_id)
    if not channel:
        raise Http404("Channel not found")

    # Step 3: Verify user has access to this channel
    if user.user_level < channel.user_level:
        logger.warning(f"[Timeshift] Access denied for user {username} to channel {channel.name}")
        return HttpResponseForbidden("Access denied")

    # Step 4: Verify channel supports timeshift
    props = stream.custom_properties or {}
    if props.get('tv_archive') not in (1, '1'):
        return HttpResponseBadRequest("Timeshift not supported for this channel")

    # Step 5: Verify it's an Xtream Codes provider
    m3u_account = stream.m3u_account
    if not m3u_account or m3u_account.account_type != 'XC':
        return HttpResponseBadRequest("Channel not from Xtream Codes provider")

    # Step 6: Convert timestamp from UTC to provider's local timezone
    # iPlayTV sends timestamps in UTC, but provider expects local time
    timezone_str = _get_plugin_timezone()
    local_timestamp = _convert_timestamp_to_local(timestamp, timezone_str)
    logger.info(f"[Timeshift] Converted timestamp: {timestamp} (UTC) -> {local_timestamp} ({timezone_str})")

    # Step 7: Build provider's timeshift URL
    # Format: /streaming/timeshift.php?username=X&password=Y&stream=Z&start=T&duration=M
    timeshift_url = (
        f"{m3u_account.server_url.rstrip('/')}/streaming/timeshift.php"
        f"?username={m3u_account.username}"
        f"&password={m3u_account.password}"
        f"&stream={props.get('stream_id')}"
        f"&start={local_timestamp}"
        f"&duration=120"  # Request 2 hours of content
    )

    logger.info(f"[Timeshift] Proxying to provider for channel: {channel.name}")

    # Step 8: Proxy the stream
    return _proxy_stream(request, timeshift_url)


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
        if xc_password and xc_password == password:
            return user
    except User.DoesNotExist:
        pass

    logger.warning(f"[Timeshift] Authentication failed for user: {username}")
    return None


def _find_channel_by_provider_stream_id(provider_stream_id):
    """
    Find channel by the provider's stream_id stored in custom_properties.

    The provider_stream_id (e.g., 22371) comes from the XC provider's API
    and is stored in stream.custom_properties.stream_id during M3U sync.
    This is different from Dispatcharr's internal channel ID.

    Returns:
        Tuple of (Channel, Stream) if found, (None, None) otherwise
    """
    from apps.channels.models import Stream

    # Search for stream where custom_properties.stream_id matches
    # Only look at XC provider streams
    stream = Stream.objects.filter(
        custom_properties__stream_id=str(provider_stream_id),
        m3u_account__account_type='XC'
    ).first()

    if stream:
        channel = stream.channels.first()
        if channel:
            return channel, stream

    logger.error(f"[Timeshift] Channel not found for provider_stream_id={provider_stream_id}")
    return None, None


def _proxy_stream(request, url):
    """
    Proxy video stream from provider to client.

    Supports HTTP Range requests for seek/forward/rewind functionality.
    iPlayTV sends Range headers when user seeks in the timeline.

    Returns:
        StreamingHttpResponse with video content (status 200 or 206)
    """
    headers = {
        'User-Agent': request.META.get('HTTP_USER_AGENT', 'Dispatcharr-Timeshift/1.0')
    }

    # Forward Range header for seek support
    # Without this, seeking in iPlayTV would fail
    range_header = request.META.get('HTTP_RANGE')
    if range_header:
        headers['Range'] = range_header

    try:
        response = requests.get(url, headers=headers, stream=True, timeout=10)

        # 200 = full content, 206 = partial content (Range request)
        if response.status_code not in (200, 206):
            logger.error(f"[Timeshift] Provider returned {response.status_code}")
            return HttpResponseBadRequest(f"Provider error: {response.status_code}")

        def stream_generator():
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
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

        logger.info("[Timeshift] Streaming started")
        return streaming_response

    except requests.exceptions.Timeout:
        logger.error("[Timeshift] Provider timeout")
        return HttpResponseBadRequest("Provider timeout")
    except requests.exceptions.RequestException as e:
        logger.error(f"[Timeshift] Provider error: {e}")
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
        if config and config.config:
            return config.config.get('timezone', 'Europe/Brussels')
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
        utc_time = utc_time.replace(tzinfo=ZoneInfo("UTC"))

        # Convert to target timezone
        local_time = utc_time.astimezone(ZoneInfo(timezone_str))

        return local_time.strftime("%Y-%m-%d:%H-%M")
    except Exception as e:
        logger.warning(f"[Timeshift] Timestamp conversion failed: {e}, using original")
        return timestamp
