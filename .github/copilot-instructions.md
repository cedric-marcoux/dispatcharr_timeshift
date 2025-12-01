# Dispatcharr Timeshift Plugin - AI Coding Instructions

## Project Overview
This is a Django plugin for [Dispatcharr](https://github.com/cedric-marcoux/dispatcharr) that adds timeshift/catch-up TV functionality to Xtream Codes IPTV providers without modifying Dispatcharr's core source code.

**Critical Context**: This entire plugin works via monkey-patching (runtime code injection) because Dispatcharr's plugin system doesn't provide hooks for URL routing or API response modification.

**Fork Notes**: Extended to support multiple catchup flag formats (not just XC native `tv_archive`) and configurable URL templates for different provider implementations.

## Architecture & Core Patterns

### Catchup Detection Strategy
The `_has_catchup_support()` helper in `hooks.py` detects timeshift/catchup from multiple M3U8 flag variations:
- XC native: `tv_archive=1`, `tv_archive_duration=7`
- M3U8 extension: `catchup-type="xc"`, `catchup-days="7"`
- Alternate: `catchup="append"`, `catchup-source="..."`
- Generic: `timeshift="7"`

Returns `(has_catchup: bool, days: int)` tuple. Used by all patches to determine if a stream supports catchup.

### URL Template System
Catchup request URLs are now configurable via plugin settings. The template system (`views.py:timeshift_proxy`) supports placeholders:
- `{server.url}`: Provider base URL
- `{XC.username}`, `{XC.password}`: Account credentials
- `{stream_id}`: Provider's stream ID
- `{program.starttime}`: UTC timestamp converted to local timezone
- `{program.duration}`: Duration in minutes (fixed at 120)

Default template maintains backward compatibility with XC native format. Pattern: load template from config → build placeholder dict → simple string replacement.

### Monkey-Patching Strategy

### Monkey-Patching Strategy
The plugin installs 5 critical patches at Django startup (`hooks.py`):

1. **`xc_get_live_streams`** - Injects `tv_archive` fields into API responses and replaces internal `stream_id` with provider's ID
2. **`stream_xc`** - Enables live streaming lookup by provider's stream_id (broken by patch #1)
3. **`xc_get_epg`** - Enables EPG lookup by provider's stream_id with custom timeshift-aware responses
4. **`generate_epg`** - Converts XMLTV timestamps from UTC to local timezone
5. **`URLResolver.resolve`** - Intercepts `/timeshift/` URLs before Django's catch-all pattern matches

**Why each patch is needed**: See extensive docstrings in `hooks.py` explaining failed alternatives (URL pattern injection, middleware, ROOT_URLCONF replacement).

### Hot Enable/Disable Pattern
Hooks install unconditionally at startup but check `_is_plugin_enabled()` at runtime. This allows toggling the plugin in Dispatcharr's UI without restarting Django. Pattern used throughout:

```python
def patched_function(...):
    if not _is_plugin_enabled():
        return _original_function(...)
    # timeshift logic here
```

### Multi-Worker Architecture
Dispatcharr runs with uWSGI's multi-worker mode. Each worker process has separate memory, so hooks install per-worker via auto-install code at module bottom (`plugin.py`).

## Critical Gotchas

### Stream ID Confusion
**The most confusing aspect of this codebase**: iPlayTV uses `stream_id` from API for BOTH live streaming and timeshift URLs, but Dispatcharr tracks two IDs:
- Internal ID (Django primary key): Used by Dispatcharr's original code
- Provider stream_id (in `custom_properties`): What XC provider expects

**Chain reaction**: Patch #1 changes API to return provider's ID → breaks live streaming → requires patch #2. Same for EPG → patch #3.

### Timeshift URL Parameter Mismatch
iPlayTV sends: `/timeshift/{user}/{pass}/{epg_channel}/{timestamp}/{provider_stream_id}.ts`

**Misleading**: The URL pattern names don't match reality:
- Parameter named `stream_id` = EPG channel number (UNUSED)
- Parameter named `duration` = Provider's stream_id (ACTUALLY USED)

See `views.py:timeshift_proxy()` for the workaround. This cannot be changed—it's how iPlayTV constructs URLs.

### Timestamp Timezone Conversion
iPlayTV sends UTC timestamps (from EPG data), but XC providers expect local time. The `timezone` setting (defaults to `Europe/Brussels`) handles conversion in `views.py:_convert_timestamp_to_local()`.

## Key Files

- **`plugin.py`**: Plugin metadata, settings schema (timezone/language/catchup_url_template), auto-install trigger
- **`hooks.py`**: All 5 monkey-patches + `_has_catchup_support()` helper + `_get_plugin_config()` helper with extensive "why" documentation
- **`views.py`**: `/timeshift/` request handler with authentication, channel lookup, URL template substitution, and stream proxying
- **`__init__.py`**: Empty (auto-install code moved to `plugin.py`)

## Development Workflows

### Testing Changes
No automated tests exist. Manual testing requires:
1. Copy plugin to Dispatcharr's `/data/plugins/dispatcharr_timeshift/`
2. Restart Dispatcharr: `docker compose restart dispatcharr`
3. Enable in UI: Settings > Plugins > Dispatcharr Timeshift
4. Test in iPlayTV client on Apple TV

**Important**: After enabling/disabling, users must refresh their IPTV source for clients to detect timeshift availability.

### Debugging
Logging pattern throughout: `logger.info/warning/error` with `[Timeshift]` prefix. Key log points:
- Hook installation: "Hooks installed successfully"
- API enhancement: "Enhanced X/Y channels with timeshift support"
- Channel lookups: "Found channel by provider stream_id=X"
- Authentication: Specific failure reasons (missing xc_password, wrong password, etc.)
- Provider errors: Include status code, content-type, body preview

### Adding New Features
When modifying patched functions:
1. **Preserve fallback behavior**: Always chain to `_original_function` when plugin disabled
2. **Update URL pattern callbacks**: If patching Django views, update both module function AND `urlpatterns` callbacks (see `_patch_stream_xc`)
3. **Store originals**: Use global `_original_*` variables for restoration
4. **Document "why"**: Explain why the approach is necessary (other approaches likely failed)

## Integration Points

### Dispatcharr Models
- `User`: Custom properties store `xc_password` (different from Django auth password)
- `Channel`: Has many `streams` via `channelstream` relationship
- `Stream`: `custom_properties` JSON contains provider metadata (`stream_id`, `tv_archive`, `tv_archive_duration`, `epg_channel_id`)
- `M3UAccount`: XC provider credentials (`account_type='XC'`)
- `PluginConfig`: Plugin settings (`key='dispatcharr_timeshift'`, `enabled`, `config`)

### External Dependencies
- `requests`: HTTP client for proxying provider streams
- `zoneinfo`: Timezone conversion (Python 3.9+ stdlib)
- Django URLResolver: Patched for URL interception

### URL Format Expectations
Client → Plugin: `/timeshift/{user}/{pass}/{epg_channel}/{timestamp}/{provider_stream_id}.ts`
Plugin → Provider: `/streaming/timeshift.php?username=X&password=Y&stream=Z&start=T&duration=120`

## Code Conventions

### Logging
- Prefix all messages with `[Timeshift]` for easy grepping
- Include context: channel names, stream IDs, usernames (no passwords!)
- Log both success paths ("Enhanced X channels") and failure diagnostics

### Error Handling
- Return HTTP 404 for "not found" (channel, user)
- Return HTTP 403 for access denied (wrong password, insufficient user level)
- Return HTTP 400 for bad requests (no timeshift support, provider errors)
- Always include actionable error messages in logs

### Configuration Access
Use `_get_plugin_config()` helper (defined in `hooks.py`) to load settings. Returns `{'timezone': str, 'language': str, 'catchup_url_template': str}` with defaults.

### Adding New Catchup Flags
To support additional catchup flag variations, update `_has_catchup_support()` in `hooks.py`:
1. Add new condition checking the flag in `custom_properties`
2. Extract `catchup-days` or equivalent duration field
3. Return `(True, days)` if flag indicates support
4. Test with provider that uses the new flag format

### Modifying URL Template Placeholders
To add new placeholders (e.g., `{channel.name}`):
1. Update `plugin.py` field help_text to document the placeholder
2. Add to `placeholders` dict in `views.py:timeshift_proxy()`
3. Ensure value is available in function scope (may need additional queries)
4. Update README.md placeholder documentation table

## Version History Context
- v1.0.3: Enhanced logging with diagnostic hints + Fork additions (flexible catchup detection and configurable URL templates)
- v1.0.2: Multi-client support (Snappier iOS, IPTVX), data type fixes, EPG timezone conversion
- v1.0.1: User-Agent fix (use M3U account setting)
- v1.0.0: Initial release

See README.md changelog for feature evolution.
