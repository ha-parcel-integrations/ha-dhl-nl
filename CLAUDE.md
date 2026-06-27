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
- Coordinator takes `config_entry=entry` so `self.config_entry` is
  available on the base class
- Per-parcel sensors are removed by the summary sensor
  (`DhlIncomingParcelsSensor`) via `entity_registry.async_remove(entity_id)`
  when a barcode drops out of the coordinator data. The earlier
  self-remove pattern raced with coordinator-listener cleanup and left
  ghost entities behind — do not revert.
- Reauth flow uses `async_update_reload_and_abort` (one helper call
  instead of update + reload + abort)
- `aiohttp.ClientError` is intentionally not caught in the coordinator —
  `DataUpdateCoordinator` wraps it automatically
- Diagnostics handler in `diagnostics.py` with credential and PII
  redaction
- Tests cover config flow, sensor, coordinator (incl. event firing),
  diagnostics, and setup/unload lifecycle
- `_unrecorded_attributes` on every summary sensor — parcel/shipment
  lists are kept out of the recorder long-term tables
- `_attr_attribution = "Data provided by DHL"` per entity

### Adopted in 2.0.0 (do not refactor away)

- **`ParcelStatus` enum** in `const.py` — canonical
  carrier-agnostic statuses. `normalize_parcel` maps the raw DHL
  status/category via `map_parcel_status` and reports `ParcelStatus.UNKNOWN`
  (with one-shot info log) for anything not yet in the map. The original
  DHL status string lives on the parcel's `raw_status` field; do not
  re-introduce it on `status`.
- **Events**: the coordinator fires `dhl_nl_parcel_registered`,
  `dhl_nl_parcel_status_changed` and `dhl_nl_parcel_delivery_time_changed`
  on the HA event bus. Events are suppressed on the very first refresh
  so we do not flood users with "registered" events for parcels that
  already existed. ``delivery_time_changed`` only fires when at least
  one of ``planned_from`` / ``planned_to`` ends up with a non-null
  value that differs from the previous one — ``value → null`` drops
  the ETA and is intentionally silent (carrier just lost the window;
  not worth a notification).
- **`has_entity_name = True`** on every entity, with `translation_key`
  routing names through `strings.json` and the language files. Drop
  `_attr_name` is the rule — translations are the source of truth.
- **Translated unit-of-measurement** (`entity.sensor.<key>.unit_of_measurement`
  in strings/translations). `_attr_native_unit_of_measurement` is
  intentionally absent.
- **`icons.json`** holds all sensor icons via the `translation_key`. Do
  not re-introduce `_attr_icon` on the sensor classes.
- **Device name pattern**: `"DHL (<email>)"`. Sensors auto-prefix with
  this, yielding friendly names like
  `DHL (account@example.com) Incoming parcels`.

### Adopted in 2.1.0 (do not refactor away)

- **Carrier-agnostic `receiver`, `weight`, `dimensions`** on every
  parcel. `receiver` is sourced from DHL's `receiver.name`; `weight` and
  `dimensions` stay `None` here because DHL's consumer API does not
  expose them — kept on the shape for parity with DPD and PostNL so the
  aggregator and cross-carrier dashboards can read every carrier the
  same way.
- **Configurable refresh interval** via the options flow
  (`CONF_REFRESH_INTERVAL`; 15, 30, 60, 120 or 240 minutes; default 30).
  The form is split into `delivered` and `polling` sections via
  `data_entry_flow.section`.
- **No `entry.add_update_listener`** — the OptionsFlow calls
  `self.hass.config_entries.async_schedule_reload(entry.entry_id)` on
  submit so a changed refresh interval takes effect immediately. Reauth
  still reloads via `async_update_reload_and_abort` (that is correct and
  unrelated). Combining an update listener with a reload-on-update flow
  is logged as a deprecation today and becomes an error in HA 2026.12+ —
  see the
  [config_entry_listener deprecation](https://developers.home-assistant.io/blog/2026/05/07/config-entry-listener-together-with-reloading-methods/).

## Planned for the next major bump

- **Exception translations** (Gold-tier rule). `UpdateFailed(f"...")`
  still uses f-strings; the Gold push will move to `translation_key` +
  `translation_placeholders`.

## Deliberately skipped (no plan to change)

- **Slimming `extra_state_attributes`** on summary sensors. The full
  parcel list stays; `_unrecorded_attributes` handles the recorder side.
- **`async-dependency` / `inject-websession`** (Platinum). The DHL API
  client is already async and accepts an injected session — no further
  work needed there. Listed for completeness.

## Repo-specific quirks

- Each config entry uses its **own aiohttp `CookieJar`** (see
  `__init__.py`) so two DHL accounts don't overwrite each other's auth
  cookies. Do not refactor this to share the HA-managed session's jar.
- `_get_en_route_parcels` / `_get_pickup_parcels` filter on
  `ParcelStatus.AT_PICKUP_POINT`. The DHL-specific raw status that maps
  to that value is `NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT`
  — kept as `STATUS_AT_SERVICE_POINT` in `const.py` for the mapping
  table in `coordinator.py`.
- Network calls return raw JSON dicts; there is no DTO layer.

## Running tests

```
python -m pytest tests/ --cov=custom_components.dhl_nl
```

Coverage must stay **above 95%** (the silver `test-coverage` rule on
developers.home-assistant.io). Run before committing.
