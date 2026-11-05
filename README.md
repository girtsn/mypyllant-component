# Vaillant Boiler Monitor

Read-only Home Assistant integration for a Vaillant ecoTEC boiler with a VR 921 gateway and an **external heating controller**, such as tado X communicating over OpenTherm. This is a specialised fork of [myVAILLANT](https://github.com/signalkraft/mypyllant-component).

## What you get

- Four hourly external recorder statistics: **Vaillant Gas Heating**, **Vaillant Gas Hot Water**, **Vaillant Electricity Heating**, and **Vaillant Electricity Hot Water**. They keep their existing `mypyllant:` statistic IDs and work in Home Assistant's Energy dashboard.
- Boiler firmware, water pressure, running hours, cycles and power **where available** from the controller-independent Vaillant cloud endpoint.
- Read-only data export and annual reports.

The integration **does not** control heating, thermostats, domestic hot water, scheduling or the OpenTherm module. Use the Tado integration for those features. Unsupported Vaillant controller platforms and configuration options are not loaded for controllerless installations.

## Energy data

Old daily-reset energy and efficiency `sensor.*` entities are removed from the controllerless setup. Their duplicate historical statistics are **not deleted**. External hourly statistics are instead published directly from the regular energy API updates, with the same statistic IDs as the previous beta release. The selection in your Energy dashboard remains valid.

Energy records start from the hourly periods available in the Vaillant cloud. For earlier history, use the separate Vaillant Probe export and review imports with Home Assistant's Import/Export Statistics utility. The cloud-reported gas primary energy is an **estimate**, not a physical gas meter.

## Installation / upgrade

Add `https://github.com/girtsn/mypyllant-component` as a custom HACS **Integration** repository. Install the current published beta release and restart Home Assistant. Do not install both this fork and upstream myVAILLANT: they use the same internal `mypyllant` domain.

Take a Home Assistant backup before upgrading. The `mypyllant` domain is deliberately kept unchanged to preserve integration configuration and the existing external statistic IDs.

## Options

Only two polling settings are presented:

- General monitoring interval, in seconds (default 1800).
- Hourly energy-data refresh interval, in seconds (default 3600).

Lowering intervals substantially can trigger Vaillant API quota limits.

## Limitations

- Data depend on Vaillant cloud availability and may be delayed or corrected.
- Controller-only values are omitted rather than presented as invented zeroes.
- The existing configuration uses a Vaillant cloud account. This integration does **not** access the Tado OpenTherm bus or provide direct flow/tank temperature when not exposed by Vaillant.

## Credits

Fork of [signalkraft/mypyllant-component](https://github.com/signalkraft/mypyllant-component), built on [myPyllant](https://github.com/signalkraft/myPyllant).
