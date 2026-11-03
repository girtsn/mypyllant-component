# v0.9.20b1 — Controllerless boiler energy support (beta)

Experimental beta of the [myVAILLANT Home Assistant integration](https://github.com/signalkraft/mypyllant-component) for boiler installations that use an independent controller, such as tado X.

## What's new

- Handle Vaillant API `controlIdentifier: none` when identifying systems, without stopping energy discovery.
- Use the controller-independent `/emf/v2/{systemId}/currentSystem` endpoint to discover supported heat-generator energy profiles.
- Reuse the integration's existing energy sensors and hourly-statistics writer for heating and domestic-hot-water primary and electrical energy.
- Keep the original discovery path for homes with supported Vaillant controllers.
- Skip controllerless homes that have no available EMF energy profiles.

## Known limitations

- This is a beta for testing, not yet confirmed against a live Home Assistant installation.
- A missing Vaillant controller cannot provide controller-dependent climate controls or tank temperature data.
- Primary energy is Vaillant-reported estimated fuel energy, **not** a direct gas meter.
- Existing hourly statistics only backfill the currently fetched daily window; importing several years of history is a separate enhancement.
- The bundled myPyllant Python dependency remains pinned to `0.9.18`.

## Installation

In HACS add `https://github.com/girtsn/mypyllant-component` as a custom **Integration** repository and select this beta release. The integration's domain remains `mypyllant`, so do not install the upstream and fork copies simultaneously. Back up your Home Assistant installation before switching repository sources.

## Development and AI disclosure

The implementation and tests were prepared with assistance from OpenAI ChatGPT, based on repository source review and anonymized read-only API results from a Vaillant ecoTEC boiler behind a VR 921 gateway. User-side live integration validation remains necessary before proposing an upstream merge.
