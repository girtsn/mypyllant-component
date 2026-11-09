# v0.9.29 — Remove deprecated Home Assistant connection-class API

- Remove the obsolete `ConfigFlow.CONNECTION_CLASS` declaration that imported `CONN_CLASS_CLOUD_POLL` and triggered a Home Assistant 2026.11 startup warning (scheduled for removal in Home Assistant 2027.5).
- Continue declaring `"iot_class": "cloud_polling"` in the integration manifest, as Home Assistant requires.
- No changes to Vaillant login, cloud polling cadence, configuration entries, entity IDs, temperature handling, or other functionality.

# v0.9.28 — Home Assistant 2026.11 climate and water-heater API

Updated every Vaillant room thermostat, Ambisense room, ventilation climate entity, and domestic hot water entity to provide native temperature units, readings and setpoints using the new Home Assistant `native_*` properties. This resolves climate and water-heater deprecation warnings (removal scheduled in Home Assistant 2027.11) without changing physical units, device commands, or entity IDs.

- The existing climate tests now verify the native temperature properties.
- Home Assistant 2026.11.0 or later is required for this release.
- HACS users: update the integration and restart Home Assistant.

# v0.9.27 — Show actual reported Vaillant fault codes

Extends the v0.9.26 **Vaillant Reported Faults** diagnostic on the existing
ecoTEC device: it now retains the actual fault information already retrieved
from `/systems/{systemId}/diagnostic-trouble-codes`.

New diagnostic attributes:
- `reported_fault_count`: total number of reported codes, unchanged
- `reported_fault_codes`: up to 20 structured entries containing only safe
  `code`, `type`, `title`, `description`, and `occurred_at` (when returned)
- `fault_codes_truncated`: whether the full list exceeds the displayed list

No serial numbers, device identifiers, article numbers or raw API payloads
are copied into Home Assistant attributes. Fault code strings, status values,
text length and timestamp formats are validated and bounded.

Upstream myPyllant test fixtures confirm the nested shape: each returned
device entry has a `codes` array, and individual codes can contain the five
fields listed above. Empty successful arrays mean no faults reported through
this API; malformed/error responses remain unavailable, never a false 'healthy'.

**No new network requests or different polling cadence:** continues using
the v0.9.26 four-hour read-only gateway diagnostics coordinator.
The existing Energy dashboard, statistics, daily sensors, entity unique IDs
and historical data are not changed. Upgrade in HACS and restart Home Assistant.
