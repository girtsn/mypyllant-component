# v0.9.24 — Vaillant Boiler Monitor

Stable, history-preserving correction for the four `mypyllant:` external energy statistics.

## Fixed

- **Recorder display names:** explicitly queue a metadata-only import for the four original external statistic IDs: **Gas Heating**, **Gas Hot Water**, **Electricity Heating**, **Electricity Hot Water**.
- Home Assistant Recorder applies the metadata update even if the hourly energy data is unchanged. Older releases only submitted new metadata with newly written energy rows, so legacy long labels could persist indefinitely.
- The update contains **zero energy samples**; no existing hourly readings, sums, statistic IDs or Energy dashboard selections are altered. It is not necessary to create new duplicate statistics.
- Metadata refresh is deferred until the four energy profiles can be discovered, and runs once per startup. It does not re-import the last hour merely to trigger a rename.
- The existing four "Today" ecoTEC device sensors and hourly collection continue as before.

## About historical backfill

This release **does not** perform an unbounded historical backfill automatically. Importing earlier hours into a cumulative series requires careful reconciliation with later sums and a user-selected historical range. Existing history is preserved so a separately tested backfill can safely fill missing periods, subject to Vaillant API retention and quota.

## Upgrade

Back up Home Assistant, install v0.9.24 in HACS and restart. Check **Developer Tools → Statistics**, filtering for `mypyllant`. The four entries should display the short names without changing statistic IDs. Keep your existing Energy dashboard sources unchanged.
