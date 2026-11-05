# v0.9.20b2 — Vaillant Boiler Monitor (beta)

Read-only controllerless boiler monitoring for VR 921 + Vaillant ecoTEC installations with independent Tado X/OpenTherm control.

- Renamed the HACS and HA integration display name to **Vaillant Boiler Monitor**; internal `mypyllant` domain unchanged.
- Removed redundant daily energy and efficiency sensor entities for controllerless installations. **Does not delete historical recorder statistics.**
- Publishes the four validated hourly energy profiles directly to Home Assistant recorder using the **same existing `mypyllant:` statistic IDs**, now with short display names.
- Limits exposed entities to controller-independent boiler diagnostics and read-only reports/exports. Climate, switches, water-heater controller platforms are not set up without a Vaillant controller.
- Only the general and energy polling intervals are configurable. No unnecessary control, holiday, schedule or brand options.
- Fixed hourly data polling to default to 3600 seconds when previously unconfigured.
- Aligns HACS display metadata with the released integration version; the release ZIP must be built from the same validated commit as the tag.

**Before upgrading:** Back up Home Assistant. After upgrading, confirm that the four hourly energy statistics continue to update, with their existing historical data and Energy dashboard settings preserved.

**No unrelated translations or multi-language changes.**

Based on upstream [myVAILLANT](https://github.com/signalkraft/mypyllant-component).
