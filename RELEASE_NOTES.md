# v0.9.25 — Vaillant Boiler Monitor

Stable read-only diagnostics for controllerless Vaillant ecoTEC / VR 921 with Tado X / OpenTherm.

## Cloud and energy connectivity (no additional requests)

- Attach **Vaillant Cloud Connection State** to the existing ecoTEC device. It is a real Home Assistant `connectivity` binary sensor: **on = cloud access worked**, **off = latest system API polling failed**, and the entity itself stays available during an outage.
- New diagnostic sensors: **Last Successful API Contact**, **Last Successful Energy Fetch**, **Latest Energy Hour**, **Cloud Consecutive Failures**. All observe existing system and hourly energy coordinators. API errors are reduced to non-sensitive failure categories.
- The Cloud Connection State entity name contains `connection_state` to allow selection by dynamic device monitoring automations. **Existing automations must also detect `on → off` transitions**; a monitor that only detects `unavailable` will not notify when this binary sensor turns off.
- Cloud access problems are **not** presented as proof that the boiler or Tado-controlled heating is offline.

## Yearly Vaillant report and Recorder reconciliation

- Reuse the validated daily consumption CSV schema from `girtsn/ha-vaillant-probe`: the four categories are heating/DHW gas and heating/DHW electricity in Wh.
- **Vaillant Historical Energy Quality** appears in the ecoTEC device diagnostics. Its state is `not_checked`, `verified`, `incomplete`, `mismatch`, or `error`; attributes include checked date range, verified/mismatch/incomplete days, missing hours, largest deviation and dates with category-specific statuses.
- Compare the Vaillant report daily totals with **existing, stable Recorder statistic IDs**, not display names. Verify both day-cumulative `state` and cumulative `sum`, with a tolerance of the greater of 10 Wh or 1% of Vaillant's daily figure. A day with 23 or 25 hours is handled properly for local DST. Unknown/missing days are never assumed zero.
- On the **third of each month**, reconcile the **previous complete calendar month**; installations that start later in the month perform that month's check once upon startup. The schedule is persisted across restarts to avoid repeated requests.
- Reports are validated and cached locally in Home Assistant's private storage; **only the relevant year** is fetched once for the automated monthly check, and no automatic retry is made if the API rejects the request. Manual checks reuse cache by default.
- Manual, read-only Home Assistant action: `mypyllant.reconcile_energy_history` with required `start_date`, `end_date` (maximum 31 days), optional `refresh_report: false`. Explicit refresh may make one GET per year and must respect Vaillant API quotas.
- **No automatic backfill or Recorder writes**. Existing four external statistic IDs, hourly collection, energy sums, names, device sensors and Energy dashboard configuration remain unchanged.

## Upgrade

Create a Home Assistant backup, install v0.9.25 through HACS, and restart. Inspect the existing ecoTEC device's Diagnostics section and use the manual action for any selected completed date range. If you use an automation that only watches `unavailable`, add a separate `off`-state condition for the Vaillant Cloud Connection State binary sensor.
