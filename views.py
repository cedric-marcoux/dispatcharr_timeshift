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

# Cache for URL format preferences per m3u_account
# Key: m3u_account.id, Value: 'A' (query string) or 'B' (path-based)
# This is session-scoped (cleared on restart) - no persistence needed
_url_format_cache = {}


def _build_timeshift_url_format_a(m3u_account, stream_id, timestamp):
    """Build timeshift URL using query string format (streaming/timeshift.php)."""
    return (
        f"{m3u_account.server_url.rstrip('/')}/streaming/timeshift.php"
        f"?username={m3u_account.username}"
        f"&password={m3u_account.password}"
        f"&stream={stream_id}"
        f"&start={timestamp}"
        f"&duration=120"
    )


def _build_timeshift_url_format_b(m3u_account, stream_id, timestamp):
    """Build timeshift URL using path format (/timeshift/user/pass/duration/time/id.ts)."""
    return (
        f"{m3u_account.server_url.rstrip('/')}/timeshift"
        f"/{m3u_account.username}"
        f"/{m3u_account.password}"
        f"/120"
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
    # QUIRK: The "duration" param is actually the provider's stream_id
    # See module docstring for explanation of iPlayTV's URL format
    provider_stream_id = duration.rstrip('.ts')

    logger.info(f"[Timeshift] Request: user={username}, provider_stream_id={provider_stream_id}, "
                f"timestamp={timestamp}, url_stream_id={stream_id}")

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

    # Step 7: Build provider's timeshift URL with format auto-detection
    # Some providers use Format A (query string), others use Format B (path-based)
    stream_id_value = props.get('stream_id')

    # Check cached format preference for this account
    preferred_format = _url_format_cache.get(m3u_account.id, 'A')

    if preferred_format == 'B':
        # Format B proven to work for this account - use directly
        timeshift_url = _build_timeshift_url_format_b(m3u_account, stream_id_value, local_timestamp)
        fallback_url = None
        logger.info(f"[Timeshift] Using cached Format B for account {m3u_account.id}")
    else:
        # Try Format A first, with Format B as fallback
        timeshift_url = _build_timeshift_url_format_a(m3u_account, stream_id_value, local_timestamp)
        fallback_url = _build_timeshift_url_format_b(m3u_account, stream_id_value, local_timestamp)

    logger.info(f"[Timeshift] Proxying to provider for channel: {channel.name}")

    # Step 8: Get User-Agent from M3U account settings
    user_agent = m3u_account.get_user_agent().user_agent

    # Step 9: Proxy the stream (with fallback support)
    return _proxy_stream(request, timeshift_url, user_agent, fallback_url, m3u_account.id)


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
            logger.warning(f"[Timeshift] Auth failed: user '{username}' has no xc_password configured")
            return None
        if xc_password != password:
            logger.warning(f"[Timeshift] Auth failed: wrong password for user '{username}'")
            return None
        return user
    except User.DoesNotExist:
        logger.warning(f"[Timeshift] Auth failed: user '{username}' does not exist")
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

    logger.debug(f"[Timeshift] Searching for provider_stream_id={provider_stream_id} in XC streams")

    # Search for stream where custom_properties.stream_id matches
    # Only look at XC provider streams
    stream = Stream.objects.filter(
        custom_properties__stream_id=str(provider_stream_id),
        m3u_account__account_type='XC'
    ).first()

    if stream:
        channel = stream.channels.first()
        if channel:
            logger.debug(f"[Timeshift] Found channel '{channel.name}' for provider_stream_id={provider_stream_id}")
            return channel, stream
        else:
            logger.error(f"[Timeshift] Stream found but no channel associated for provider_stream_id={provider_stream_id}")
    else:
        logger.error(f"[Timeshift] Channel not found: provider_stream_id={provider_stream_id}. "
                    f"Check: Is stream synced? Is M3U account type 'XC'? "
                    f"Does stream.custom_properties.stream_id exist?")

    return None, None


def _proxy_stream(request, url, user_agent, fallback_url=None, m3u_account_id=None):
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

    Returns:
        StreamingHttpResponse with video content (status 200 or 206)
    """
    # Log URL without credentials for security
    url_base = url.split('?')[0]
    logger.info(f"[Timeshift] Proxying to provider: {url_base}")

    headers = {
        'User-Agent': user_agent
    }

    # Forward Range header for seek support
    # Without this, seeking in iPlayTV would fail
    range_header = request.META.get('HTTP_RANGE')
    if range_header:
        headers['Range'] = range_header
        logger.debug(f"[Timeshift] Forwarding Range header: {range_header}")

    try:
        response = requests.get(url, headers=headers, stream=True, timeout=10)

        # If 400 error and we have a fallback URL, try the alternative format
        if response.status_code == 400 and fallback_url:
            logger.info(f"[Timeshift] Format A returned 400, trying Format B...")
            response.close()

            fallback_url_base = fallback_url.split('?')[0]
            logger.info(f"[Timeshift] Proxying to provider (fallback): {fallback_url_base}")

            response = requests.get(fallback_url, headers=headers, stream=True, timeout=10)

            # If fallback works, cache the format preference
            if response.status_code in (200, 206) and m3u_account_id:
                _url_format_cache[m3u_account_id] = 'B'
                logger.info(f"[Timeshift] Format B works for account {m3u_account_id}, caching preference")

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

        logger.info(f"[Timeshift] Streaming started (status={response.status_code}, "
                   f"content-type={response.headers.get('Content-Type', 'unknown')})")
        return streaming_response

    except requests.exceptions.Timeout:
        logger.error(f"[Timeshift] Provider timeout after 10s for {url_base}")
        return HttpResponseBadRequest("Provider timeout")
    except requests.exceptions.ConnectionError as e:
        logger.error(f"[Timeshift] Provider connection error for {url_base}: {e}")
        return HttpResponseBadRequest("Provider connection error")
    except requests.exceptions.RequestException as e:
        logger.error(f"[Timeshift] Provider request error for {url_base}: {e}")
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
        result = local_time.strftime("%Y-%m-%d:%H-%M")

        logger.debug(f"[Timeshift] Timestamp: {timestamp} (UTC) -> {result} ({timezone_str})")
        return result
    except Exception as e:
        logger.warning(f"[Timeshift] Timestamp conversion failed for '{timestamp}': {e}")
        return timestamp
