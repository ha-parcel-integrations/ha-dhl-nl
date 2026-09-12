# Architecture

How `ha-dhl-nl` is built and why it is built that way. `CLAUDE.md` is the short
list of things not to get wrong; this file is the structure and the reasoning
behind it. API mechanics — endpoints, params, content types and the DHL
status/category vocabulary — live in the private
`carrier-research/dhl/api/dhl-nl/` and are never copied here.

The shaping constraint: DHL exposes **two account endpoints** that both feed the
same entity set, and a webshop return arrives on the *receiver* endpoint rather
than the sender one. That is why there are two coordinators sharing one client,
and why "outgoing" is assembled from both.

## Project layout

```
custom_components/dhl_nl/
├── __init__.py          setup/teardown, wires client + both coordinators, first refresh
├── api.py               HTTP client: login, parcels, sent shipments, track & trace
├── const.py             URLs, ACTIVE_CATEGORIES, polling constants, option keys
├── coordinator.py       both DataUpdateCoordinators: polling, filtering, events
├── parcels.py           pure filter/normalise/sort helpers over raw parcel dicts
├── config_flow.py       setup, options, reauth
├── sensor.py            summary + per-parcel + outgoing + diagnostic sensors
├── button.py            refresh button
├── calendar.py          read-only deliveries calendar
├── device.py            shared device-info helper
├── device_trigger.py    exposes bus events as device automation triggers
├── diagnostics.py       redacted diagnostics dump
├── manifest.json / icons.json / strings.json
└── translations/        en.json, nl.json
```

`PLATFORMS` is `[Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]`. There is
no `services.py` — the account feed is the source of parcels, so there is nothing
to track or untrack by hand.

## Runtime data

Config state lives on the entry, not in `hass.data`:

```python
@dataclass
class DhlData:
    client: DhlApiClient
    coordinator: DhlCoordinator
    sent_coordinator: DhlSentShipmentsCoordinator
    user_info: dict[str, Any]        # login response: userId, email, firstName, …
    session: aiohttp.ClientSession   # per-entry, closed on unload

type DhlConfigEntry = ConfigEntry[DhlData]
```

The session is **per entry** with its own cookie jar, so two DHL accounts cannot
clobber each other's auth cookies, and it is closed on every failed-setup path
as well as on unload.

## Data flow

```
DHL eCommerce NL API
        │
        ▼
  DhlApiClient (api.py)          one client, one session, one cookie jar
  ┌─────────────────────────────────┐
  │ async_login()                   │  POST /api/user/login
  │ async_get_parcels()             │  GET  /receiver-parcel-api/parcels
  │ async_get_sent_shipments()      │  GET  /api/orders/sentShipments
  └─────────────────────────────────┘
        │                    │
        ▼                    ▼
DhlCoordinator      DhlSentShipmentsCoordinator
        │                    │
        │  incoming: filter_active_parcels / filter_delivered_parcels
        │  outgoing: filter_active_returns / filter_delivered_returns
        │            (all four over the SAME parcels payload)
        │                    │  own-sender shipments (~always empty)
        └─────────┬──────────┘
                  ▼
        merged at the sensor layer
                  │
                  ▼
        Home Assistant entity registry
```

Both coordinators share one `DhlApiClient`, so they share the session and cookie
jar — a re-authentication by one refreshes the session for the other.

## Component responsibilities

### `__init__.py`
Creates one `DhlApiClient` and performs the initial login, creates both
coordinators, runs the **first refresh before forwarding platforms**, and stores
everything on `entry.runtime_data`. On unload it tears down platforms and closes
the per-entry session.

### `api.py`
Thin async wrapper over the DHL eCommerce NL API. Uses a per-entry
`aiohttp.ClientSession` so cookies persist, extracts the `XSRF-TOKEN` cookie
from the jar and adds it as a header on every non-login call. Raises
`DhlAuthError` on login failure and `DhlApiError` on other non-200 responses.
Session recovery lives here, not in the coordinators.

### `coordinator.py`
`DhlCoordinator` polls `async_get_parcels()` and applies all four filters to the
same raw payload: `filter_active_parcels()` / `filter_delivered_parcels()` (both
exclude returns) for the incoming lists, `filter_active_returns()` /
`filter_delivered_returns()` (both require `isReturn`) for the return lists,
stored as `returning` / `delivered_outgoing`.

`DhlSentShipmentsCoordinator` polls `async_get_sent_shipments()` and applies
`filter_active_sent_shipments()` / `filter_delivered_sent_shipments()`. This is
the account holder's own DHL-registered shipments; it never contains
webshop-generated return labels, because the account holder is not their sender
of record — in practice both lists stay empty for a consumer account.

`_apply_delivered_filter(parcels, entry)` and `_delivery_dt(parcel)` are shared
module-level helpers so both coordinators apply the same days/count option. The
thin `DhlCoordinator._apply_delivered_filter` wrapper is kept because tests call
it.

Both coordinators raise `UpdateFailed` on any `DhlApiError` or
`aiohttp.ClientError`.

### `sensor.py`
| Class | Reads |
|---|---|
| `DhlIncomingParcelsSensor` | summary of active incoming; also owns per-parcel entity lifecycle |
| `DhlParcelSensor` | one entity per active incoming parcel, keyed by barcode |
| `DhlNextDeliverySensor` | earliest `receivingTimeIndication.moment` across active parcels (TIMESTAMP) |
| `DhlEnRouteToServicePointSensor` | active pickup-point parcels not yet notified |
| `DhlPickupPendingSensor` | active pickup-point parcels already notified (arrived, awaiting collection) |
| `DhlDeliveredParcelsSensor` | `coordinator.delivered` |
| `DhlSentShipmentsSensor` | `sent_coordinator.data` + `coordinator.returning` |
| `DhlOutgoingDeliveredSensor` | `sent_coordinator.delivered` + `coordinator.delivered_outgoing` |
| `DhlLastUpdateSensor` | diagnostic TIMESTAMP over `coordinator.last_success_time` |

Neither outgoing sensor creates per-shipment entities.

## Dynamic polling

Account-based model, **unconditional** status-driven polling. There is no
polling option, no `CONF_REFRESH_INTERVAL`, and no `POLL_INTERVAL` fallback
constant; a stale `refresh_interval` left in an entry's stored options is
simply never read.

**Each coordinator recomputes its own `update_interval`** at the end of its own
`_async_update_data`, independently. There is no shared scheduling point: they
are two separate `DataUpdateCoordinator` instances polled on their own timers,
each seeded at `HOT_INTERVAL_MINUTES` in its constructor so the first poll after
setup is prompt. (The refresh button triggers both together; the automatic timer
does not.)

- **Hot tier** (`HOT_INTERVAL_MINUTES` 15) the moment any active parcel is
  `out_for_delivery`, starting an hour before `planned_from`, or immediately when
  `planned_from` is missing.
- **Mid tier** (`MID_INTERVAL_MINUTES` 45) otherwise. It never stops: this is an
  account-based carrier, so the mid-tier poll is the only way a new shipment gets
  discovered. `problem` and `returning` stay mid, deliberately not hot.
- **Quiet window** 00:00–06:00 local (`QUIET_WINDOW_START_HOUR` /
  `QUIET_WINDOW_END_HOUR`) with anchor polls at each end.
- **Stagger**: a small deterministic per-`entry_id` offset, the same for both
  coordinators since they share the entry id.

`DhlCoordinator`'s hottest-status scan covers incoming (`coordinator.data`) **and**
outgoing/returning (`self.returning`) — a return that is `out_for_delivery` must
drive the tier hot too. `DhlSentShipmentsCoordinator` scans its own active sent
shipments separately, which in practice means it sits at the mid-tier floor.

Surfaced in diagnostics under `"polling"`: `current_tier_minutes` /
`update_interval_seconds` for the main coordinator,
`sent_current_tier_minutes` / `sent_update_interval_seconds` for the sent one.

**Do not add a refresh-interval option back.** The suite converged on a single
polling behaviour (maintainer decision, 2026-09-12); a per-carrier dropdown is
drift, not a feature.

## Key design decisions

### Two coordinators, one client

Both coordinators share a single `DhlApiClient`, and therefore one `aiohttp`
session and cookie jar. A re-authentication triggered by one also refreshes the
session for the other.

### Session recovery lives in the client

`async_get_parcels` and `async_get_sent_shipments` retry once after a fresh
`async_login()` on HTTP 401/403. An `asyncio.Lock` on the client ensures that
when both coordinators hit a 401 at the same moment, only one re-login happens.
If the retry also fails, `DhlApiError` propagates to the coordinator, which
raises `UpdateFailed`, and HA marks the integration unavailable until the next
poll.

### Auth-error split

Only a 401/403 login rejection escalates to `ConfigEntryAuthFailed` and reauth.
Any other failure — a 5xx outage, for instance — becomes `ConfigEntryNotReady`
and backs off. **Never collapse these**: a DHL outage must not force every user
through reauth.

### Dynamic per-parcel sensor lifecycle

`DhlIncomingParcelsSensor` tracks a `_known_barcodes` set. On each coordinator
update it diffs current barcodes against the known set, calls
`async_add_entities` for new ones, and removes stale ones from the entity
registry. Parcel sensors therefore appear and disappear without an HA restart.

**The summary sensor does the removal**, via `entity_registry.async_remove` — the
older self-removal raced with listener cleanup and left ghost entities.

### Setup stale-entity cleanup is sensor-scoped

Filter `entity_entry.domain == "sensor"` before treating a `{user_id}_*`
unique_id as a barcode, else the sweep deletes the refresh button. Non-parcel
sensor unique_ids (`_refresh`, `_last_update`, `_outgoing_parcels`,
`_outgoing_delivered_parcels`, …) **must** stay in `non_parcel_unique_ids`.

### Returns are outgoing, and they come from the parcels list

A return label generated by a webshop makes the account holder the *receiver* of
the original parcel and the *sender* of the return — but not the sender of record
on DHL's own-shipments API, so `async_get_sent_shipments()` never lists it.

The receiver-parcel-api's `parcels` list, however, already includes both
directions: normal incoming parcels (`isReturn: false`) and outgoing returns
(`isReturn: true`), the latter distinguished by
`destination.locationType: "RETURN"` and, once complete,
`isReturnedToShipper: true`. `filter_active_returns()` /
`filter_delivered_returns()` split the return parcels out of that same list,
mirroring the incoming filters. That is why returns are computed on
`DhlCoordinator` rather than on `DhlSentShipmentsCoordinator`.

**Externally, a return is just outgoing — not a separate concept.** `isReturn`
and `isReturnedToShipper` drive internal filter logic only; they never leak into
entity names. `DhlSentShipmentsSensor` (`outgoing_parcels`) and
`DhlOutgoingDeliveredSensor` (`outgoing_delivered_parcels`) each merge data from
*both* coordinators and re-sort with `sort_parcels_by_ts`. This mirrors how
PostNL treats "outgoing" as one concept regardless of how a parcel became
outgoing, and keeps DHL's entity naming identical to PostNL's so cross-carrier
tooling needs no DHL-specific special-casing. A DHL-specific
`returning_parcels` / `delivered_outgoing_parcels` naming was considered and
rejected for exactly this reason. A separate "return" sensor was tried and
reverted.

Because both outgoing sensors read from two coordinators, they are
`CoordinatorEntity[DhlCoordinator]` for the main one and additionally subscribe
to `DhlSentShipmentsCoordinator` in `async_added_to_hass`
(`async_on_remove(sent_coordinator.async_add_listener(self.async_write_ha_state))`).
**Don't drop that subscription** or they go stale.

### Filtering happens at the coordinator, not the sensor

Filtering — active categories, return splitting — happens in the coordinator.
This keeps sensors simple and makes the filtered data the single source of truth
for every entity.

### History is opt-in, default OFF

`CONF_INCLUDE_HISTORY` exposes a top-level `history` key (it survives the
aggregator's `strip_raw()`) and is `None` when off — the key is never omitted.

Cost control: `_history_cache`; `_enrich_history` runs only for **active plus
delivered incoming**, and fetches only on first sight of a barcode or a
raw-status change. History status reuses the parcel maps — don't extend them per
event. **Returns fetch no history**, because the track-trace call is
receiver-role.

## Events

Incoming events run over **active plus delivered** combined, so the terminal hop
is visible: a change *to* DELIVERED fires only `_delivered`; an already-delivered
barcode fires nothing; `registered` fires only for not-yet-delivered new
barcodes. `delivery_time_changed` fires only when a `planned_*` becomes non-null
*and* differs — a `value → null` transition is intentionally silent. State lives
in `_known_state` / `_known_delivery_times`.

Outgoing events (`dhl_nl_outgoing_parcel_status_changed` /
`_outgoing_parcel_delivered`) run over `returning + delivered_outgoing`;
`delivered` wins the terminal hop; there is **no** outgoing `registered` or
`delivery_time_changed`. State lives in `_known_outgoing_state`. The source is
`DhlCoordinator` (returns), not the sent coordinator, since own-sender is
practically always empty for consumers.

`device_id` is on every payload, resolved once and cached in `_cached_device_id`.
`device_trigger.py` exposes all bus events under
`device_automation.trigger_type`.

Unmapped statuses log once per distinct value with an `issues/new` link
(`_NEW_ISSUE_URL`); the one-shot sets are `_unmapped_statuses_logged` /
`_unmapped_event_keys_logged`. The `en_route` / `awaiting_pickup` sensors split
on `ParcelStatus.AT_PICKUP_POINT`.

## Sensor unique ID patterns

| Entity | Unique ID | Example |
|---|---|---|
| `DhlIncomingParcelsSensor` | `{user_id}_incoming_parcels` | `abc123_incoming_parcels` |
| `DhlParcelSensor` | `{user_id}_{barcode}` | `abc123_JVGL0123456789012345` |
| `DhlNextDeliverySensor` | `{user_id}_next_delivery` | `abc123_next_delivery` |
| `DhlEnRouteToServicePointSensor` | `{user_id}_en_route_to_service_point` | `abc123_en_route_to_service_point` |
| `DhlPickupPendingSensor` | `{user_id}_pickup_pending` | `abc123_pickup_pending` |
| `DhlDeliveredParcelsSensor` | `{user_id}_delivered_parcels` | `abc123_delivered_parcels` |
| `DhlSentShipmentsSensor` | `{user_id}_outgoing_parcels` | `abc123_outgoing_parcels` |
| `DhlOutgoingDeliveredSensor` | `{user_id}_outgoing_delivered_parcels` | `abc123_outgoing_delivered_parcels` |
| `DhlLastUpdateSensor` | `{user_id}_last_update` | `abc123_last_update` |
| `DhlRefreshButton` | `{user_id}_refresh` | `abc123_refresh` |
| `DhlDeliveriesCalendar` | `{user_id}_deliveries` | `abc123_deliveries` |

`user_id` comes from the login response and is a UUID.

## Other surfaces

- **Refresh button** — `async_press` refreshes **both** coordinators.
- **Diagnostic `last_update` sensor** — TIMESTAMP, DIAGNOSTIC, reading
  `coordinator.last_success_time` (stamped at the end of a successful
  `_async_update_data`), so users can alert on a silently stale integration.
- **Deliveries calendar** — read-only over `coordinator.data`, **no extra API
  calls**, enabled by default. A cross-carrier calendar belongs in the
  aggregator.
- **Diagnostics** redact credentials and PII: `name` (raw payloads) and the
  normalized `receiver` are in `TO_REDACT`. Over-redact — diagnostics get pasted
  into public issues.
- **Fields**: `receiver` is populated; `weight` / `dimensions` stay `None` (the
  consumer API omits them) but the keys exist for cross-carrier parity, and
  `const.py`'s `CAPABILITIES` reflects that.

## Adding a new endpoint

1. Add the URL constant to `const.py`.
2. Add an `async_get_*` method to `DhlApiClient` in `api.py`.
3. Add a filter function in `parcels.py` and, if it needs its own cadence, a new
   `DataUpdateCoordinator` subclass in `coordinator.py`.
4. Construct it in `__init__.py` and add it to `DhlData`.
5. Add the sensor class(es) in `sensor.py`, subscribing to the extra coordinator
   in `async_added_to_hass` if it reads from more than one.
