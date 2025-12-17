# Dispatcharr Timeshift Plugin

Timeshift/catch-up TV plugin for Dispatcharr. Watch past TV programs (up to 7 days) via Xtream Codes providers.

**Version**: 1.1.8
**GitHub**: https://github.com/cedric-marcoux/dispatcharr_timeshift
**License**: MIT

---

## ⚠️ IMPORTANT: Installation Methods

### Method 1: Git Clone (Recommended)

The **easiest and most reliable** way to install:

```bash
cd /path/to/dispatcharr/data/plugins/
git clone https://github.com/cedric-marcoux/dispatcharr_timeshift.git
docker compose restart dispatcharr
```

Then enable the plugin in Dispatcharr **Settings → Plugins**.

### Method 2: Download Release Asset

Download `dispatcharr_timeshift.zip` from the [Releases page](https://github.com/cedric-marcoux/dispatcharr_timeshift/releases), then import via **Settings → Plugins → Import**.

### Method 3: Manual ZIP (Advanced)

⚠️ **WARNING**: GitHub's default "Download ZIP" includes the branch/tag name in the folder (e.g., `dispatcharr_timeshift-1.1.8/` or `dispatcharr_timeshift-main/`), which **breaks Python imports**. You must rename the folder:

```bash
cd /path/to/dispatcharr/data/plugins/

# If downloaded from a release tag (v1.1.8):
unzip dispatcharr_timeshift-1.1.8.zip
mv dispatcharr_timeshift-1.1.8 dispatcharr_timeshift

# If downloaded from main branch:
unzip dispatcharr_timeshift-main.zip
mv dispatcharr_timeshift-main dispatcharr_timeshift

# Fix permissions
chmod 644 dispatcharr_timeshift/*.py
chown 1000:1000 dispatcharr_timeshift/*
docker compose restart dispatcharr
```

### Timeshift Not Working?

If timeshift features don't appear after installation, your **provider may not support timeshift** (tv_archive). Check if your Xtream Codes provider offers catch-up/replay functionality.

---

## Changelog

### v1.1.8
- **Documentation**: Improved installation instructions
  - Method 1: Git clone (recommended) - most reliable method
  - Method 2: Download release asset `dispatcharr_timeshift.zip` (correct folder name)
  - Method 3: Manual ZIP with folder rename warning
  - Explained why GitHub's default ZIP breaks Python imports (folder name includes version/branch)

### v1.1.7
- **Bug fix**: Export Plugin class in `__init__.py`
  - Dispatcharr requires the Plugin class to be exported from `__init__.py`
  - Without this, the plugin fails with "invalid plugin: missing plugin class"
  - Added `from .plugin import Plugin` and `__all__ = ['Plugin']`

### v1.1.6
- **New feature: Debug Mode** - Toggle in plugin settings to enable ultra-verbose logging
  - Normal mode: Minimal logging (1 line per timeshift request + errors only)
  - Debug mode: Detailed logs for every step (config loading, channel lookup, timestamp conversion, URL building, provider response)
- **New feature: URL Format Selection** - Choose between timeshift URL formats:
  - Auto-detect (default): Tries Format A, falls back to Format B if 400 error
  - Format A: Query string (`/streaming/timeshift.php?username=X&...`)
  - Format B: Path-based (`/timeshift/user/pass/duration/time/id.ts`)
  - Custom: User-defined template with placeholders
- **New feature: Custom URL Template** - For exotic providers with non-standard URLs
  - Placeholders: `{server_url}`, `{username}`, `{password}`, `{stream_id}`, `{timestamp}`, `{duration}`
  - Only used when "Custom template" is selected in URL Format
- **New feature: Timezone Dropdown** - Provider timezone now uses a dropdown with 120 IANA timezone options
  - Organized by region: UTC, Europe, Americas, Asia, Africa, Australia/Pacific
  - Prevents typos and invalid timezone entries
- **Code cleanup**: Added `.strip()` to all config values to prevent whitespace issues
- **Reduced log noise**: Production logs now minimal, detailed info only in debug mode
- **Bug fix**: XMLTV EPG compatibility with Dispatcharr v0.14 (handles both HttpResponse and StreamingHttpResponse)

### v1.1.5
- **Bug fix**: Re-enabled UTC→Local timezone conversion for timeshift timestamps
  - v1.1.4 incorrectly removed the conversion, causing wrong content to play
  - Root cause: IPTV clients (iPlayTV, TiviMate, Televizo) use `start_timestamp` (UTC unix timestamp) from EPG to construct timeshift URLs
  - The timestamp in the URL is therefore in UTC, but XC providers expect LOCAL time
  - Now views.py correctly converts the timestamp from UTC to the configured timezone
  - Example: User selects 18:00 Toronto show → Client sends 23:00 UTC → Plugin converts to 18:00 local → Provider plays correct content

### v1.1.4 (BROKEN - DO NOT USE)
- ~~Bug fix: Removed double timezone conversion~~ - This was incorrect
  - This version broke timeshift for all non-UTC timezones
  - Users experienced wrong content playing (offset by their timezone difference)
  - Fixed in v1.1.5

### v1.1.3
- **Bug fix**: Timezone setting was not being read from database
  - Plugin was using wrong attribute `config.config` instead of `config.settings`
  - Timezone always defaulted to "Europe/Brussels" regardless of user setting
  - Now correctly reads from Dispatcharr's PluginConfig.settings field
  - Affects both timeshift URL conversion and EPG timestamp conversion

### v1.1.2
- **Code cleanup**: Removed dead code (`uninstall_hooks()` and `_restore_*()` functions)
  - Dispatcharr never calls `plugin.run("disable")`, so these functions were never executed
- **Optimized diagnostics**: Expensive DB queries in 404 handler now only run in DEBUG mode
  - Reduces overhead on production systems
  - Basic warning still logged at INFO level
- **Minor fix**: Removed unnecessary `if chunk:` check in stream generator
- **Tested with Dispatcharr v0.14.0**

### v1.1.1
- **Dynamic EPG-based duration**: Timeshift requests now use the actual programme duration from EPG
  - Calculates duration from programme's `end_time - start_time`
  - Adds 5-minute buffer for stream startup
  - Falls back to 120 minutes if programme not found in EPG
  - Caps at 8 hours maximum to prevent issues
  - Prevents long movies from being cut off (was hardcoded to 2h)
  - More efficient for short programmes (30-45 min)

### v1.1.0
- **URL format fallback**: Automatic detection and fallback for providers using different timeshift URL formats
  - Format A (default): `/streaming/timeshift.php?username=X&password=Y&stream=Z&start=T&duration=N`
  - Format B (fallback): `/timeshift/{username}/{password}/N/{timestamp}/{stream_id}.ts`
  - Automatically tries Format B if Format A returns HTTP 400
  - Caches working format per M3U account for session (no restart needed)
  - Fixes "Provider returned 400" error for providers using path-based timeshift URLs

### v1.0.5
- **Enhanced diagnostics**: Improved "Channel not found" logging with detailed troubleshooting info
  - Shows if stream exists but with wrong account type (need 'XC')
  - Shows if stream has no channels assigned
  - Shows total XC streams count (helps detect sync issues)
  - Shows if channel exists but user lacks access level

### v1.0.4
- **Bug fix**: Fixed `AssertionError: .accepted_renderer not set on Response` error
  - Replaced Django REST Framework `Response` with Django `JsonResponse` in `patched_stream_xc()`
  - This error occurred when channel lookup failed (404) or credentials were invalid (401)
  - The issue was that DRF's Response requires a renderer, but the patched function is called from Django URL patterns, not through DRF's APIView

### v1.0.3
- **Enhanced logging**: Improved error diagnostics with detailed context for troubleshooting
  - API requests now log channel enhancement stats (e.g., "Enhanced 36/36 channels")
  - EPG requests log channel lookups and program generation counts
  - Authentication failures now specify the exact reason (missing xc_password, wrong password, unknown user)
  - Provider errors include status code, content-type, and response body preview
  - All errors include actionable diagnostic hints

### v1.0.2
*Based on fixes from [Lesthat's fork](https://github.com/Lesthat/dispatcharr_timeshift) - thanks for the contributions!*

- **Multi-client support**: Added compatibility for Snappier iOS and IPTVX
- **EPG 404 fix**: Fixed "not found" errors when clients request EPG data using provider stream IDs
- **Data type fixes**: Corrected JSON types for strict validation (Snappier iOS compatibility)
- **EPG timezone fix**: Programs now display at correct times (fixed +2h offset issue)
- **XMLTV timezone**: Converted timestamps for IPTVX compatibility
- **Language setting**: Configurable EPG language (27 European languages)
- **Unique program IDs**: Each program now has a timestamp-based unique ID

### v1.0.1
- **User-Agent fix**: Now uses the User-Agent configured in M3U account settings (TiviMate, VLC, etc.) instead of a hardcoded value

### v1.0.0
- Initial release

> **IMPORTANT**: After enabling or disabling this plugin, you must **refresh your source** in your IPTV player (e.g., iPlayTV) for it to detect the timeshift/replay availability on channels.

## Features

- **100% Plugin Solution** - No modification to Dispatcharr source code required
- **Multi-Client Compatible** - Works with iPlayTV, Snappier iOS, IPTVX, and other Xtream Codes clients
- **Seek Support** - Forward/rewind via HTTP Range headers
- **Auto-install** - Hooks install automatically on startup
- **Hot Enable/Disable** - Enable or disable without restarting Dispatcharr
- **Timezone Conversion** - Configurable timezone for accurate playback positioning

## Requirements

1. Dispatcharr installed and running
2. Xtream Codes provider with timeshift support (`tv_archive=1`)
3. Channels synced with EPG data

## Installation

1. Copy `dispatcharr_timeshift/` folder to Dispatcharr's `/data/plugins/`
2. Restart Dispatcharr
3. Enable in Dispatcharr UI: Settings > Plugins > Dispatcharr Timeshift
4. Configure timezone if needed (defaults to Europe/Brussels)

## How It Works

### The Challenge

Dispatcharr doesn't natively support timeshift. Adding this feature as a plugin presented several challenges:

1. **Catch-all URL pattern**: Dispatcharr has a `<path:unused_path>` pattern that catches all unmatched URLs
2. **Stream ID mismatch**: iPlayTV uses the `stream_id` from the API for timeshift URLs, but Dispatcharr returns internal IDs
3. **Timezone differences**: iPlayTV sends UTC timestamps, but providers expect local time
4. **Multi-worker architecture**: uWSGI runs multiple workers, each needs hooks installed

### The Solution: Monkey-Patching

We use five monkey-patches to add timeshift without modifying Dispatcharr's source:

#### 1. Patch `xc_get_live_streams` (API Response)

**Problem**: The Xtream Codes API response doesn't include `tv_archive` fields, so iPlayTV doesn't know which channels support timeshift.

**Solution**: Patch the function to:
- Add `tv_archive` and `tv_archive_duration` from stream's `custom_properties`
- Replace Dispatcharr's internal `stream_id` with the provider's `stream_id`

```python
# Before patch: stream_id = 42 (Dispatcharr internal ID)
# After patch:  stream_id = 22371 (Provider's ID)
#               tv_archive = 1
#               tv_archive_duration = 7
```

#### 2. Patch `stream_xc` (Live Streaming)

**Problem**: After changing `stream_id` to provider's ID in the API, live streaming breaks. iPlayTV requests `/live/user/pass/22371.ts` but Dispatcharr looks up `Channel.objects.get(id=22371)` which doesn't exist.

**Solution**: Patch `stream_xc` to first search by provider's `stream_id` in `custom_properties`, then fall back to internal ID lookup.

**Additional Challenge**: Simply patching the function in the module doesn't work because Django URL patterns keep a reference to the original function from import time. We must also update `pattern.callback` directly in `urlpatterns`.

#### 3. Patch `xc_get_epg` (EPG Data)

**Problem**: After changing `stream_id` to provider's ID, EPG requests fail. Clients request EPG using provider's stream_id, but Dispatcharr looks up by internal ID.

**Solution**: Patch `xc_get_epg` to first search by provider's `stream_id` in `custom_properties`, then fall back to internal ID lookup. Also generates custom EPG with correct data types for strict clients like Snappier iOS.

#### 4. Patch `generate_epg` (XMLTV Timezone)

**Problem**: IPTVX and some clients display EPG timestamps as-is without timezone conversion, causing programs to appear at wrong times.

**Solution**: Patch `generate_epg` to convert XMLTV timestamps from UTC to the configured local timezone.

#### 5. Patch `URLResolver.resolve` (Timeshift URLs)

**Problem**: Timeshift URLs like `/timeshift/user/pass/155/2025-01-15:14-30/22371.ts` are caught by Dispatcharr's catch-all pattern before any plugin URL can match.

**Why Other Approaches Failed**:
- URL pattern injection (`urlpatterns.insert`) - Catch-all still matched
- Middleware - Runs after URL resolution, too late
- ROOT_URLCONF replacement - Django caches settings at startup

**Solution**: Patch `URLResolver.resolve()` to intercept URLs BEFORE pattern matching happens.

### Request Flow

```
iPlayTV Client
    │
    ▼
/timeshift/user/pass/155/2025-01-15:14-30/22371.ts
    │
    ▼
URLResolver.resolve [PATCHED] ─── Intercepts /timeshift/ URLs
    │
    ▼
timeshift_proxy()
    ├── 1. Authenticate user (xc_password)
    ├── 2. Find channel by provider stream_id (22371)
    ├── 3. Check user access level
    ├── 4. Verify tv_archive support
    ├── 5. Convert timestamp UTC → Local timezone
    ├── 6. Get programme duration from EPG
    └── 7. Proxy stream to client
    │
    ▼
Provider: /streaming/timeshift.php?stream=22371&start=2025-01-15:14-30&duration={EPG_DURATION}
```

## Configuration

### Plugin Settings

| Setting | Default | Description |
|---------|---------|-------------|
| Provider Timezone | Europe/Brussels | Timezone for timestamp conversion (IANA format) |
| EPG Language | en | Language code for EPG data (27 European languages available) |
| Debug Mode | Off | Enable ultra-verbose logging for troubleshooting |
| Catchup URL Format | Auto-detect | URL format for timeshift requests (see below) |
| Custom URL Template | (empty) | Custom URL with placeholders (only when "Custom" format selected) |

### URL Format Options

| Format | URL Pattern | When to Use |
|--------|-------------|-------------|
| **Auto-detect** (default) | Tries A, falls back to B | Most providers - works automatically |
| **Format A** | `/streaming/timeshift.php?username=X&password=Y&stream=Z&start=T&duration=N` | Standard XC providers |
| **Format B** | `/timeshift/{user}/{pass}/{duration}/{timestamp}/{stream_id}.ts` | Some providers require this format |
| **Custom** | User-defined template | Exotic providers with non-standard URLs |

### Custom URL Template Placeholders

If your provider uses a non-standard URL format, select "Custom template" and use these placeholders:

| Placeholder | Value |
|-------------|-------|
| `{server_url}` | Provider's server URL (without trailing slash) |
| `{username}` | M3U account username |
| `{password}` | M3U account password |
| `{stream_id}` | Provider's stream ID |
| `{timestamp}` | Programme start time (YYYY-MM-DD:HH-MM format, local timezone) |
| `{duration}` | Programme duration in minutes |

Example custom template:
```
{server_url}/catchup/{username}/{password}/{stream_id}/{timestamp}/{duration}.m3u8
```

### Timezone Setting

iPlayTV sends timestamps in UTC (from EPG data), but Xtream Codes providers expect local time. Configure the timezone to match your provider's location.

Common values:
- `Europe/Brussels` - Belgium, Western Europe
- `Europe/Paris` - France
- `America/New_York` - US Eastern
- `America/Los_Angeles` - US Pacific

## iPlayTV Configuration

1. Open iPlayTV on Apple TV
2. Add new source > Xtream Codes
3. Configure:
   - **Server URL**: `http://your-dispatcharr-ip:9191`
   - **Username**: Your Dispatcharr username
   - **Password**: Your `xc_password` (from user custom properties, NOT Django password)

## Technical Details

### URL Format from iPlayTV

```
/timeshift/{username}/{password}/{epg_channel}/{timestamp}/{provider_stream_id}.ts
```

Example:
```
/timeshift/john/secret123/155/2025-01-15:14-30/22371.ts
```

**Important**: The parameter names are misleading due to how iPlayTV constructs URLs:
- Position 3 (`stream_id` in pattern) = EPG channel number (NOT used)
- Position 5 (`duration` in pattern) = Provider's stream_id (USED for lookup)

### Stream ID Explained

Two different IDs are involved:

| ID Type | Example | Where Used |
|---------|---------|------------|
| Dispatcharr Internal ID | 42 | Database primary key |
| Provider Stream ID | 22371 | Stored in `stream.custom_properties.stream_id` |

The plugin modifies the API to return provider's stream_id so iPlayTV builds correct timeshift URLs.

### uWSGI Multi-Worker Architecture

Dispatcharr runs with multiple uWSGI workers (separate processes). Each worker has its own memory space, so:

- Hooks must be installed in EACH worker independently
- The plugin auto-installs on first request to each worker
- Warm-up requests ensure all workers are ready (see Troubleshooting)

### Runtime Enable/Disable (Hot Toggle)

The plugin supports enabling/disabling without restarting Dispatcharr:

- Hooks are installed once at startup (regardless of plugin enabled state)
- Each hook checks the database `enabled` flag at runtime before executing
- When disabled, hooks pass through to original Dispatcharr functions
- No restart required - changes take effect immediately

**Why this approach?**

Dispatcharr's PluginManager only toggles the `enabled` flag in the database when you enable/disable a plugin. It does NOT call `plugin.run("enable")` or `plugin.run("disable")`. So we can't rely on those callbacks to install/uninstall hooks dynamically. Instead, hooks are always installed but check the enabled state per-request.

**Note**: After toggling the plugin, refresh your source in iPlayTV to see the updated channel list (with or without timeshift support).

### File Structure

```
dispatcharr_timeshift/
├── __init__.py   # Package marker
├── plugin.py     # Plugin metadata, settings, auto-install on startup
├── hooks.py      # Five monkey-patches (API, live stream, EPG, XMLTV, URL resolver)
├── views.py      # Timeshift proxy with timezone conversion
└── README.md     # This file
```

## FAQ

### Does the plugin proxy the stream or pass the provider URL to the client?

**The plugin proxies all streams through Dispatcharr.** It does NOT pass the provider's catch-up URL directly to the client.

How it works:
1. Client sends request to Dispatcharr: `/timeshift/user/pass/.../stream_id.ts`
2. Plugin intercepts the request
3. Dispatcharr fetches the stream from the XC provider
4. Stream is proxied back to the client

**VPN use case**: If you need all XC provider traffic to go through a VPN, simply connect Dispatcharr to the VPN. All timeshift (and live) streams will be fetched through the VPN, while your clients connect directly to Dispatcharr without needing VPN apps.

### Does timeshift work with custom channels that have multiple streams?

Yes, but only the **first stream** (by priority order) determines timeshift availability. The plugin checks `tv_archive` from the first stream's `custom_properties`.

### What about channels with mixed sources (Xtream + non-Xtream)?

Timeshift will only appear if the **first priority stream** is from an Xtream Codes provider with `tv_archive=1`.

Example scenarios:
- Stream #1 is XC with timeshift → ✅ Timeshift appears
- Stream #1 is non-XC, Stream #2 is XC with timeshift → ❌ Timeshift does NOT appear

**Important**: For timeshift to actually work when playing, the XC stream with timeshift support must be the one being played. If a non-XC stream takes priority during playback, timeshift won't function even if shown in the channel list.

**Recommendation**: For channels where you want timeshift, ensure the XC stream with `tv_archive=1` is set as the **first priority** stream.

### Does catchup/timeshift work with Emby Live TV or other M3U-based players?

**No, catchup does not work when using Dispatcharr's M3U output.**

Dispatcharr generates a "clean" M3U without catchup attributes:
```
#EXTINF:-1 tvg-id="10" tvg-name="|BE| LA UNE FHD" ...
http://dispatcharr:9191/proxy/ts/stream/uuid
```

For M3U-based catchup to work, the following attributes would be required:
```
catchup="default"
catchup-source="http://server/timeshift/user/pass/{stream_id}/{start}/{duration}.ts"
catchup-days="7"
```

**The Timeshift plugin only works with:**
- Xtream Codes API (`player_api.php`) - adds `tv_archive=1` to responses
- Direct `/timeshift/...` URL interception
- IPTV clients that use the XC API (iPlayTV, TiviMate in XC mode, Snappier, etc.)

**For M3U-based players like Emby Live TV, your options are:**
1. Use your provider's original M3U directly (bypass Dispatcharr for catchup)
2. Connect via Xtream Codes API instead of M3U if the player supports it
3. Request Dispatcharr to add catchup support in M3U export (feature request on their GitHub)

The Timeshift plugin patches the XC API layer, but Dispatcharr's M3U generator is a separate core component that doesn't include catchup metadata.

### My programs show 2 hours off in Snappier/IPTVX

Ensure the "Provider Timezone" setting matches your provider's timezone. Most European providers use "Europe/Brussels" or similar. If programs appear 2 hours early or late, adjust the timezone setting accordingly.

### I get 404 errors when selecting a program for replay

This was fixed in v1.0.2. The issue occurred because clients use provider stream IDs for EPG requests, but Dispatcharr was looking up by internal IDs. Update to v1.0.2 or later.

## Troubleshooting

### Channels don't show timeshift option in iPlayTV

Each uWSGI worker needs to install hooks on its first request. Warm up all workers:

```bash
for i in {1..10}; do curl -s http://localhost:9191/api/channels/ -o /dev/null; done
```

### "Channel not found" error

The provider's `stream_id` must be stored in `stream.custom_properties`. This happens automatically during M3U sync for Xtream Codes providers. Try re-syncing your M3U account.

### Wrong program plays (time offset)

Check timezone configuration. If you request 13:00 news but get 11:00 content, the timezone offset is wrong. Adjust the "Provider Timezone" setting in plugin configuration.

### Live channels return 404

This can happen if hooks aren't fully installed. The plugin patches both the `stream_xc` function AND the URL pattern callback. Restart Dispatcharr to ensure clean hook installation.

### Check logs

The plugin uses structured logging with different levels for easy troubleshooting:

```bash
# All timeshift logs
docker compose logs dispatcharr | grep -i timeshift

# Specific events
docker compose logs dispatcharr | grep "Timeshift.*API"           # API enhancements (channel counts)
docker compose logs dispatcharr | grep "Timeshift.*EPG"           # EPG lookups and generation
docker compose logs dispatcharr | grep "Timeshift.*Live"          # Live stream lookups
docker compose logs dispatcharr | grep "Timeshift.*Request"       # Incoming timeshift requests
docker compose logs dispatcharr | grep "Timeshift.*Auth"          # Authentication issues
docker compose logs dispatcharr | grep "Timeshift.*Provider"      # Provider communication errors
```

**Log levels (Normal mode):**
- `INFO`: One line per timeshift request (`[Timeshift] TF1 @ 2025-01-15:14-30`)
- `ERROR`: Failures requiring attention (auth failed, channel not found, provider errors)

**Log levels (Debug mode - enable in plugin settings):**
- All of the above, plus:
- Detailed config loading
- Channel search steps (provider_stream_id lookup, internal_id fallback)
- Stream properties and tv_archive status
- Timestamp conversion details (UTC → Local)
- URL format selection and built URL
- Request headers and provider response status
- Each step is logged with `=== REQUEST START ===` and `=== REQUEST END ===` markers

## Limitations

1. **Worker warm-up required**: Each uWSGI worker must handle at least one request to install hooks
2. **XC providers only**: Only works with Xtream Codes type M3U accounts
3. **EPG required for accurate duration**: Without EPG data, falls back to 120 minutes

## Development Notes

### Why Monkey-Patching?

We explored several approaches before settling on monkey-patching:

1. **URL pattern injection** - Failed because catch-all pattern matches first
2. **Middleware** - Failed because it runs after URL resolution
3. **ROOT_URLCONF replacement** - Failed because Django caches settings
4. **Django signals** - No suitable signal for URL interception
5. **Monkey-patching URLResolver.resolve** - Works!

### Key Insight: Django URL Pattern Callbacks

When patching a view function, you must also patch the URL pattern's callback:

```python
# This alone is NOT enough:
proxy_views.stream_xc = patched_stream_xc

# Must also update URL patterns:
for pattern in main_urls.urlpatterns:
    if pattern.callback == _original_stream_xc:
        pattern.callback = patched_stream_xc
```

Django resolves function references at import time and stores them in `pattern.callback`. Patching the module doesn't affect already-resolved patterns.

## License

MIT License - See LICENSE file for details.
