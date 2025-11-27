# Dispatcharr Timeshift Plugin

Timeshift/catch-up TV plugin for Dispatcharr. Watch past TV programs (up to 7 days) via Xtream Codes providers.

**Version**: 1.0.0
**GitHub**: https://github.com/cedric-marcoux/dispatcharr_timeshift
**License**: MIT

> **IMPORTANT**: After enabling or disabling this plugin, you must **refresh your source** in your IPTV player (e.g., iPlayTV) for it to detect the timeshift/replay availability on channels.

## Features

- **100% Plugin Solution** - No modification to Dispatcharr source code required
- **iPlayTV/Apple TV Compatible** - Works with Xtream Codes protocol
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

We use three monkey-patches to add timeshift without modifying Dispatcharr's source:

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

#### 3. Patch `URLResolver.resolve` (Timeshift URLs)

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
    └── 6. Proxy stream to client
    │
    ▼
Provider: /streaming/timeshift.php?stream=22371&start=2025-01-15:14-30&duration=120
```

## Configuration

### Plugin Settings

| Setting | Default | Description |
|---------|---------|-------------|
| Provider Timezone | Europe/Brussels | Timezone for timestamp conversion (IANA format) |

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
├── hooks.py      # Three monkey-patches (API, live stream, URL resolver)
├── views.py      # Timeshift proxy with timezone conversion
└── README.md     # This file
```

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

```bash
# All timeshift logs
docker compose logs dispatcharr | grep -i timeshift

# Specific events
docker compose logs dispatcharr | grep "Timeshift.*Intercepted"   # URL interception
docker compose logs dispatcharr | grep "Timeshift.*Converted"     # Timezone conversion
docker compose logs dispatcharr | grep "Timeshift.*Live"          # Live stream lookup
```

## Limitations

1. **Worker warm-up required**: Each uWSGI worker must handle at least one request to install hooks
2. **Fixed duration**: Proxy requests 2 hours of content from provider
3. **XC providers only**: Only works with Xtream Codes type M3U accounts

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
