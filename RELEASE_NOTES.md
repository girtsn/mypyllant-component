# v0.9.22 — Vaillant Boiler Monitor

Stable hotfix for controllerless Vaillant ecoTEC with VR 921, including independent Tado X / OpenTherm heating control.

- **Restore boiler-device energy readings:** register all four supported energy profiles from the gateway's EMF system description at startup. An empty or delayed hourly energy response no longer prevents those four sensors being created.
- The four **Today** readings belong to the existing boiler device and show **kWh** when hourly data is available. During an API outage, they remain registered, showing unavailable rather than disappearing.
- **Safer migration:** remove only unsupported control-platform entities and positively identified obsolete daily energy / efficiency sensors. Do not remove unknown, disabled or temporarily unavailable diagnostic sensors.
- Defer the Recorder display-name migration until the hourly publisher has energy profiles, rather than consuming its opportunity on an empty startup response.
- Preserve unchanged `mypyllant:` hourly energy statistic IDs, recorded values, historical Energy dashboard configuration, polling, and controller-independent behavior.

**After upgrading:** restart Home Assistant. Confirm the ecoTEC device lists the four Today readings and that the long-term hourly statistics continue receiving data. No user-created Recorder statistics should be deleted. Backup before upgrading.

Based on [myVAILLANT](https://github.com/signalkraft/mypyllant-component).

The device readings depend on the current hourly API response; historical consumption remains in the four separate Recorder statistic series.
