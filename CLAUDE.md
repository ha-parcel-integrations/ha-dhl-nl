# Working in this repository

Home Assistant custom integration for DHL eCommerce NL parcel tracking.
Distributed via HACS; not part of HA core. **Silver** quality tier,
minimum HA `2024.7.0`. No DTO layer — network calls return raw JSON dicts.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change first-refresh or unmapped-status logging | *Parcel contract* (this repo implements it; below is only where DHL deviates) |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**API mechanics live in `docs/api/` (local-only, gitignored)** — the parcels /
sent-shipments / track-trace endpoints, their params and content types, and the
DHL status/category → `ParcelStatus` vocabulary. Do not duplicate them here; this
file is HA-integration decisions only.

**Suite-wide tripwire, kept inline on purpose:** the first refresh runs in
`__init__.py` *before* `async_forward_entry_setups`, never in a platform — from a
forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
entry. Runtime-only; the tests don't catch a regression here.

## Load-bearing DHL decisions — do not refactor away

**Setup & lifecycle**
- **Per-entry `ClientSession`, closed on every failed-setup path** (login fail,
  first-refresh fail, platform-forward fail) — else each retry leaks a session.
- **Per-entry aiohttp `CookieJar`** so two DHL accounts don't clobber each
  other's auth cookies. Don't share the HA-managed session's jar.
- **Auth-error split**: only a 401/403 login rejection escalates to
  `ConfigEntryAuthFailed` (reauth); any other failure (e.g. a 5xx outage) →
  `ConfigEntryNotReady` (backoff). **Never collapse these** — a DHL outage must
  not force reauth.
- `aiohttp.ClientError` is intentionally **not** caught in the coordinator
  (`DataUpdateCoordinator` wraps it).
- **Config**: `ConfigEntry.runtime_data` (typed `DhlData`),
  `PARALLEL_UPDATES = 0`, coordinators take `config_entry=entry`.
- **Reauth** uses `async_update_reload_and_abort`; the confirm step guards with
  `async_set_unique_id` + `_abort_if_unique_id_mismatch` so a *different*
  account's credentials abort instead of silently rebinding.
- **Options flow** has no `entry.add_update_listener` — it calls
  `async_schedule_reload` on submit. `CONF_REFRESH_INTERVAL` =
  15/30/60/120/240 min, default 30.

**Entities & naming**
- **`has_entity_name = True`** everywhere; names route through `translation_key`
  → `strings.json`/language files. No `_attr_name`. Icons in `icons.json`,
  unit-of-measurement translated — no `_attr_icon`, no
  `_attr_native_unit_of_measurement`.
- `_attr_attribution = "Data provided by DHL"` per entity. **Device name**
  `"DHL (<email>)"`; sensors auto-prefix it.
- **Per-parcel sensors are removed by the summary sensor**
  (`DhlIncomingParcelsSensor`) via `entity_registry.async_remove` when a barcode
  drops out. The old self-remove raced with listener cleanup and left ghosts —
  do not revert.
- **Setup stale-entity cleanup is sensor-scoped**: filter
  `entity_entry.domain == "sensor"` before treating a `{user_id}_*` unique_id as
  a barcode, else it deletes the refresh button. Non-parcel sensor unique_ids
  (`_refresh`, `_last_update`, `_outgoing_parcels`,
  `_outgoing_delivered_parcels`, …) **must** stay in `non_parcel_unique_ids`.
- **Recorder**: `_unrecorded_attributes` keeps parcel/shipment lists (and
  per-parcel `history`) out of long-term tables. Not slimming
  `extra_state_attributes` further is deliberate.
- **`receiver`** is populated; **`weight`/`dimensions` stay `None`** (consumer API
  omits them) but the keys exist for cross-carrier parity.
- Unmapped statuses log once per distinct value with an `issues/new` link
  (`_NEW_ISSUE_URL`); one-shot sets `_unmapped_statuses_logged` /
  `_unmapped_event_keys_logged`. The `en_route` / `awaiting_pickup` sensors split
  on `ParcelStatus.AT_PICKUP_POINT`.

**History (opt-in, default OFF — `CONF_INCLUDE_HISTORY`)**
- Top-level `history` (survives the aggregator's `strip_raw()`); `None` when off
  (key never omitted).
- **Cost control**: `_history_cache`; `_enrich_history` runs only for
  **active + delivered incoming**, and only fetches on first sight of a barcode or
  a raw-status change. History status reuses the parcel maps (don't extend them
  per event). **Returns fetch no history** (the track-trace call is receiver-role).

**Outgoing = own-sender shipments + folded-in returns**
- A webshop return makes the account the *receiver*, so returns never come via the
  sent-shipments call — they're split out of the parcels list into
  `DhlCoordinator.returning` / `.delivered_outgoing`.
- **The return flag is an internal filter, never an entity name.** A separate
  "return" sensor was tried and reverted — externally a return is just another way
  a parcel is *outgoing* (PostNL's model). Merge return-adjacent data into the
  existing outgoing sensors.
- `DhlSentShipmentsSensor` (`_outgoing_parcels`) merges `sent_coordinator.data` +
  `coordinator.returning`; `DhlOutgoingDeliveredSensor`
  (`_outgoing_delivered_parcels`) merges `sent_coordinator.delivered` +
  `coordinator.delivered_outgoing`. Both re-sort with `sort_parcels_by_ts` and
  **subscribe to `sent_coordinator`** in `async_added_to_hass` — don't drop it or
  they go stale. `_apply_delivered_filter` / `_delivery_dt` are module-level so the
  sent coordinator reuses the filter; keep the thin
  `DhlCoordinator._apply_delivered_filter` wrapper (tests call it).

**Events** (generic contract in CONVENTIONS.md; DHL specifics here)
- Incoming events run over **active + delivered** combined so the terminal hop is
  visible: a change **to** DELIVERED fires only `_delivered`; an already-delivered
  barcode fires nothing; `registered` only for not-yet-delivered new barcodes.
  `delivery_time_changed` fires only when a `planned_*` becomes non-null and
  differs — `value → null` is intentionally silent. State in `_known_state` /
  `_known_delivery_times`.
- Outgoing: `dhl_nl_outgoing_parcel_status_changed` / `_outgoing_parcel_delivered`
  over `returning + delivered_outgoing`; `delivered` wins the terminal hop.
  **No** outgoing `registered` / `delivery_time_changed`. State in
  `_known_outgoing_state`. Source is `DhlCoordinator` (returns), not the sent
  coordinator (own-sender is ~always empty for consumers).
- `device_id` on every payload (resolved once, cached in `_cached_device_id`);
  `device_trigger.py` exposes all bus events under `device_automation.trigger_type`.

**Other surfaces**
- **Refresh `button`** (`{user_id}_refresh`): `async_press` refreshes **both**
  coordinators.
- **Diagnostic `last_update` sensor** (`{user_id}_last_update`, TIMESTAMP,
  DIAGNOSTIC) reads `coordinator.last_success_time` (stamped at the end of a
  successful `_async_update_data`) — lets users alert on a silently stale
  integration.
- **Deliveries `calendar`** (`{user_id}_deliveries`): read-only over
  `coordinator.data`, **no extra API calls**, enabled by default. A cross-carrier
  calendar belongs in the aggregator.
- **Diagnostics** redact credentials + PII: `name` (raw payloads) and normalized
  `receiver` are in `TO_REDACT`.

## Planned / skipped

- **Planned (next major)**: exception translations — `UpdateFailed(f"...")`
  moves to `translation_key` + `translation_placeholders` (Gold rule).
- **Skipped on purpose**: `async-dependency` / `inject-websession` (Platinum) —
  client is already async and accepts an injected session.

## Running tests

```
python -m pytest tests/ --cov=custom_components.dhl_nl
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing.
