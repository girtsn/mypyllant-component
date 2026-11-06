# v0.9.23 — Vaillant Boiler Monitor

Stable corrective release for controllerless ecoTEC with VR 921 gateway and independent Tado X/OpenTherm heating control.

**Root cause corrected:** the pinned myPyllant 0.9.18 model exposes the profile kind from EMF `currentSystem` as `value_type`, while hourly bucket results may use `energy_type`. Earlier controllerless releases filtered the system definitions by `energy_type` only, resulting in no active energy profiles, no boiler device sensors and unchanged long Recorder labels.

- Discover supported Gas Heating, Gas Hot Water, Electricity Heating and Electricity Hot Water profiles using either field.
- Fetch the four actual hourly energy bucket series and carry the requested profile kind and device identity into the results when Vaillant omits them, retaining original Recorder statistic IDs.
- Register the four non-statistical, read-only Today sensors on the existing boiler device, showing kWh once hourly measurements are received.
- Update the existing Recorder series names on import to short labels without renaming statistic IDs, resetting sums or deleting history.
- Retain the conservative entity cleanup introduced in v0.9.22, which does not remove unknown diagnostic sensors.
- Added regression tests for the real gateway-style `valueType` payload and the polling, publishing and device-discovery paths.

**Upgrade:** Back up Home Assistant. Install this version in HACS and restart Home Assistant. The four Recorder statistics should start receiving hourly data again and should acquire their shorter display names when updated. Their historical statistic IDs and Energy dashboard settings remain unchanged.

Based on [myVAILLANT](https://github.com/signalkraft/mypyllant-component).
