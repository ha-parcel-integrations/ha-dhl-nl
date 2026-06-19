# Working in this repository

This is a Home Assistant custom integration for DHL eCommerce NL parcel
tracking. Distributed via HACS; not part of HA core.

## Always consult HA developer documentation

Home Assistant's integration patterns evolve continuously. **Do not rely
on memory of past patterns** — fetch the canonical page before changing
a topic area, and check the developer blog before introducing anything
you only "know" from training data.

| When you change | Fetch first |
|---|---|
| Entity properties, naming, lifecycle, attributes | https://developers.home-assistant.io/docs/core/entity/ |
| Sensor specifics (state/device classes, units) | https://developers.home-assistant.io/docs/core/entity/sensor |
| Config flow, options flow, reauth, reconfigure | https://developers.home-assistant.io/docs/config_entries_config_flow_handler |
| DataUpdateCoordinator pattern | https://developers.home-assistant.io/docs/integration_fetching_data |
| Quality scale rules | https://developers.home-assistant.io/docs/core/integration-quality-scale |
| Diagnostics | https://developers.home-assistant.io/docs/core/integration/diagnostics |
| Translations | https://developers.home-assistant.io/docs/internationalization/core |

Branding is handled by the local `brand/` folder (HACS reads `icon.png`
from it). The official `home-assistant/brands` repo is for HA Core
integrations and does not apply here.

### Recent developer-facing changes

Before introducing patterns you only know from training data, check:

- https://developers.home-assistant.io/blog — API deprecations, new
  patterns, breaking changes. Recent posts trump older recollection.
- https://github.com/home-assistant/architecture/discussions — design
  decisions in flight that have not made it into stable docs yet.

## What is already in place

The integration is aligned with the **silver** quality scale tier. Don't
re-propose these as improvements:

- `quality_scale: "silver"` in manifest, minimum HA version `2024.7.0`
- `ConfigEntry.runtime_data` (typed dataclass `DhlData`)
- `PARALLEL_UPDATES = 0` in `sensor.py`
- Per-parcel sensors self-remove via `async_remove(force_remove=True)`
- Coordinator logs warnings on unavailability (auth and connectivity)
- Reauth flow calls `async_reload` so new credentials propagate to the
  in-memory `DhlApiClient`
- Diagnostics handler in `diagnostics.py` with credential and PII
  redaction
- Tests cover config flow, sensor, coordinator, diagnostics, and
  setup/unload lifecycle (≥75% required for silver)
- `_unrecorded_attributes` on every summary sensor — parcel/shipment
  lists are kept out of the recorder long-term tables
- `_attr_attribution = "Data provided by DHL"` per entity

## What was deliberately skipped

- **`has_entity_name`** is *not* used on this integration. Switching to
  it would change friendly names for existing dashboards and automations.
  The user weighed this trade-off explicitly. Do not change it without
  asking.
- **Slimming `extra_state_attributes`** on summary sensors. The full
  parcel list stays; `_unrecorded_attributes` handles the recorder side.

## Repo-specific quirks

- Each config entry uses its **own aiohttp `CookieJar`** (see
  `__init__.py`) so two DHL accounts don't overwrite each other's auth
  cookies. Do not refactor this to share the HA-managed session's jar.
- The `_get_en_route_parcels` / `_get_pickup_parcels` filters look at
  `STATUS_AT_SERVICE_POINT` (the magic string from `const.py`) — if DHL
  changes the wording in a future API update, that's the place to fix.
- Network calls return raw JSON dicts; there is no DTO layer.

## Running tests

```
python -m pytest tests/ --cov=custom_components.dhl_nl
```

Coverage must stay ≥75% (silver requirement). Run before committing.
