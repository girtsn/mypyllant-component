# v0.9.21 — Vaillant Boiler Monitor

Stable release for read-only Vaillant ecoTEC / VR 921 monitoring with independent Tado X / OpenTherm control.

- Remove orphaned controller-only controls, unsupported diagnostics, and old daily energy entities from Home Assistant's entity registry. Supported firmware and boiler diagnostics stay visible.
- Preserve the four existing `mypyllant:` hourly energy statistic IDs, their recorded history, and Energy dashboard configuration.
- Refresh historical Recorder series display names to **Gas Heating**, **Gas Hot Water**, **Electricity Heating** and **Electricity Hot Water**. IDs are intentionally unchanged.
- Add four compact, read-only **Today** energy sensors to the boiler device page, in kWh. These do **not** create duplicate long-term statistics.
- HACS now recognizes stable published releases rather than showing the `main` branch commit hash.
- Keep the existing 60-minute energy poll, with the previously configured general polling interval.

**After installing:** Restart Home Assistant, confirm four `mypyllant:` statistics still update and are renamed, and check that the obsolete device entries disappear. Take a backup before installing.

Based on upstream [myVAILLANT](https://github.com/signalkraft/mypyllant-component).
