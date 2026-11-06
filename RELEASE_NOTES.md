# v0.9.26 — VR 921 gateway diagnostics

Adds three controller-independent, read-only diagnostic binary sensors to the
existing ecoTEC device: **Vaillant Gateway Online**, **Vaillant Reported Faults**
(with fault-count attribute), and **Vaillant EEBUS Enabled** (with capability
and SPINE status attributes).

These values are grounded in cloud GET endpoints confirmed by Vaillant Probe
v0.6.2 on the VR 921. They do not require a Vaillant heating controller.

- Diagnostics refresh every 4 hours using the existing authenticated API
  session, independently of hourly energy collection.
- API/quota/authentication errors leave the affected diagnostic **unavailable**;
  an empty successful fault list means no faults reported through the API.
- EEBUS enabled/capable is an interface status, not a connected SPINE peer or
  a guarantee that the boiler exposes temperatures.
- Excludes empty RTS, MPC, PV and efficiency datasets and avoids creating
  placeholder sensors for unavailable information.
- No boiler control operations, no energy statistics writes, and **no changes**
  to the four stable `mypyllant:` energy statistic IDs or dashboard history.

Upgrade through HACS and restart Home Assistant. Review the ecoTEC device's
Diagnostics section. Keep existing failure monitors: `Vaillant Cloud Connection
State` and `Vaillant Gateway Online` mean different things.
