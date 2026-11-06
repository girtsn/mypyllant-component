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
