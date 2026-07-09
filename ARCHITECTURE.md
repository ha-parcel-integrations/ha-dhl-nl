# Architecture

This document describes the internal structure of the DHL NL integration, how the components relate to each other, and the key design decisions made. It is intended as a reference for AI agents and contributors working on the codebase.

## Project layout

```
custom_components/dhl_nl/
├── __init__.py        # Entry point: setup, teardown, wires up client + coordinators
├── api.py             # HTTP client: login, fetch parcels, fetch sent shipments
├── config_flow.py     # UI config flow: initial setup + re-authentication
├── const.py           # All constants: URLs, domain, poll interval, active categories
├── coordinator.py     # DataUpdateCoordinator subclasses: polling + filtering
├── sensor.py          # Sensor entities: summary + per-parcel + outgoing summary
├── manifest.json      # HA integration manifest
├── strings.json       # UI strings (source of truth, duplicated into translations/)
└── translations/
    ├── en.json        # English translations
    └── nl.json        # Dutch translations
```

## Data flow

```
DHL eCommerce NL API
        │
        ▼
  DhlApiClient (api.py)
  ┌─────────────────────────────────┐
  │ async_login()                   │  POST /api/user/login
  │ async_get_parcels()             │  GET  /receiver-parcel-api/parcels
  │ async_get_sent_shipments()      │  GET  /api/orders/sentShipments
  └─────────────────────────────────┘
        │                    │
        ▼                    ▼
DhlCoordinator      DhlSentShipmentsCoordinator
(coordinator.py)    (coordinator.py)
  polls every 30 min, filters both incoming
  parcels AND outgoing returns (isReturn) out
  of the same parcels list
        │                    │
        └─────────┬──────────┘
                   ▼
       merged at the sensor layer:
  DhlPackagesSensor      DhlSentShipmentsSensor        (outgoing_parcels)
  DhlParcelSensor        DhlOutgoingDeliveredSensor    (outgoing_delivered_parcels)
  DhlNextDeliverySensor
  DhlPickupPendingSensor
  DhlDeliveredParcelsSensor
  (sensor.py)
        │
        ▼
  Home Assistant entity registry
```

## Component responsibilities

### `__init__.py`
- Called by HA when the integration is loaded (`async_setup_entry`)
- Creates one `DhlApiClient`, performs the initial login
- Creates one `DhlCoordinator` and one `DhlSentShipmentsCoordinator`, both sharing the same `DhlApiClient`
- Stores everything in `hass.data[DOMAIN][entry.entry_id]`
- Forwards setup to the `sensor` platform
- On unload (`async_unload_entry`), tears down platforms and cleans up `hass.data`

### `api.py`
- Thin async HTTP wrapper around the DHL eCommerce NL API
- Uses the HA-managed `aiohttp.ClientSession` so cookies persist automatically
- Extracts the `XSRF-TOKEN` cookie from the jar and adds it as a request header on each non-login call
- Raises `DhlAuthError` on login failure, `DhlApiError` on other non-200 responses
- Handles session re-authentication internally: `async_get_parcels` and `async_get_sent_shipments` retry once after a fresh login on HTTP 401/403; an `asyncio.Lock` prevents concurrent re-login attempts when both coordinators hit a 401 simultaneously

### `config_flow.py`
- Implements the HA UI config flow (`async_step_user`)
- Validates credentials using the HA-managed shared session (`async_get_clientsession`) before saving
- Sets the unique ID to the account email to prevent duplicate entries
- Implements `async_step_reauth` / `async_step_reauth_confirm` for the HA re-auth flow

### `const.py`
- Single source of truth for all magic values
- `ACTIVE_CATEGORIES` — the frozenset used by both coordinators to filter out delivered/terminal shipments
- `POLL_INTERVAL` — 1800 seconds (30 minutes), used by both coordinators

### `coordinator.py`
- `DhlCoordinator` — polls `async_get_parcels()`, applies `filter_active_parcels()` / `filter_delivered_parcels()` (both exclude returns) for the incoming lists, and `filter_active_returns()` / `filter_delivered_returns()` (both require `isReturn`) for the return lists — all four filter the *same* raw parcels payload. Return lists are stored as `returning` / `delivered_outgoing`
- `DhlSentShipmentsCoordinator` — polls `async_get_sent_shipments()`, applies `filter_active_sent_shipments()` / `filter_delivered_sent_shipments()` (`data` / `delivered`). This is the account holder's own DHL-registered shipments; it never contains webshop-generated return labels, since the account holder isn't their sender of record — in practice both lists stay empty
- `_apply_delivered_filter(parcels, entry)` / `_delivery_dt(parcel)` are shared module-level helpers so both coordinators apply the same days/count delivered-filter option
- Both coordinators raise `UpdateFailed` on any `DhlApiError` or `aiohttp.ClientError`; session recovery is handled by the client, not the coordinators

### `sensor.py`
- `DhlPackagesSensor` — summary sensor for incoming parcels; also manages the lifecycle of `DhlParcelSensor` entities (creates new ones, removes stale ones from the entity registry on each coordinator update)
- `DhlParcelSensor` — one entity per active incoming parcel, keyed by barcode
- `DhlNextDeliverySensor` — derives the earliest `receivingTimeIndication.moment` across all active parcels; device class `TIMESTAMP` for native HA datetime handling
- `DhlEnRouteToServicePointSensor` — counts active parcels destined for a ServicePoint where status is not yet `NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT`
- `DhlPickupPendingSensor` — counts active parcels at a ServicePoint with status `NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT`, meaning they have arrived and the recipient has been notified
- `DhlDeliveredParcelsSensor` — recently delivered incoming parcels (`coordinator.delivered`)
- `DhlSentShipmentsSensor` — active **outgoing** parcels: `sent_coordinator.data` (own-sender shipments, almost always empty) merged with `coordinator.returning` (return parcels, the data that actually populates this sensor in practice)
- `DhlOutgoingDeliveredSensor` — delivered outgoing parcels: `sent_coordinator.delivered` merged with `coordinator.delivered_outgoing`
- Neither outgoing sensor creates per-shipment entities; both bind to `DhlCoordinator` for update notifications (see "Returns are outgoing" below for why)

## Key design decisions

### Two coordinators, one client
Both coordinators share a single `DhlApiClient` instance. This means they share the same `aiohttp` session and cookie jar, so a re-authentication by one coordinator also refreshes the session for the other.

### Dynamic per-parcel sensor lifecycle
`DhlPackagesSensor` tracks a `_known_barcodes` set. On each coordinator update it diffs the current barcodes against the known set, calls `async_add_entities` for new ones, and removes stale ones from the entity registry. This means parcel sensors appear and disappear automatically without requiring an HA restart.

### No per-shipment sensors for outgoing
Outgoing parcels (own sent shipments and returns alike) are exposed as a single summary sensor with a list attribute, not per-item sensors. This is intentional — they are less frequently monitored than incoming parcels and the data is fully accessible via the attribute. Adding per-item sensors would follow the same pattern as `DhlParcelSensor` if needed in the future.

### Returns are outgoing, and they come from the parcels list, not the sent-shipments endpoint
A return label generated by a webshop makes the account holder the *receiver* of the original parcel and the *sender* of the return — but not the sender of record on DHL's own-shipments API, so `async_get_sent_shipments()` never lists it. The receiver-parcel-api's `parcels` list, however, already includes both directions: normal incoming parcels (`isReturn: false`) and outgoing returns (`isReturn: true`), distinguishing itself with `destination.locationType: "RETURN"` and (once complete) `isReturnedToShipper: true`. `filter_active_returns()` / `filter_delivered_returns()` split the return parcels out of that same list by category, mirroring `filter_active_parcels()` / `filter_delivered_parcels()` for incoming. This is why returns are computed on `DhlCoordinator` (`returning`, `delivered_outgoing`) rather than on `DhlSentShipmentsCoordinator`.

**Externally, a return is just outgoing — not a separate concept.** `isReturn` / `isReturnedToShipper` only drive the internal filter logic; they do not leak into entity names. `DhlSentShipmentsSensor` (`outgoing_parcels`) and `DhlOutgoingDeliveredSensor` (`outgoing_delivered_parcels`) each merge data from *both* coordinators — the account holder's own sent shipments plus the return parcels — into one list, re-sorted with `sort_parcels_by_ts`. This mirrors how PostNL treats "outgoing" as a single concept regardless of how a parcel became outgoing, and keeps DHL's entity naming identical to PostNL's so cross-carrier tooling (e.g. the hki-parcels-card Lovelace card) needs no DHL-specific special-casing. A DHL-specific `returning_parcels` / `delivered_outgoing_parcels` naming was considered and rejected for exactly this reason.

Both outgoing sensors read from *two* coordinators. They are `CoordinatorEntity[DhlCoordinator]` for the main coordinator and additionally subscribe to `DhlSentShipmentsCoordinator` in `async_added_to_hass` (`async_on_remove(sent_coordinator.async_add_listener(self.async_write_ha_state))`), so an update from either source refreshes the sensor.

### Filtering at the coordinator level
Filtering (active categories, return exclusion) happens in the coordinator, not in the sensor. This keeps sensors simple and ensures the filtered data is the single source of truth for all entities.

### Session recovery
Session recovery is handled entirely inside `DhlApiClient`. The `async_get_parcels` and `async_get_sent_shipments` methods retry once after a fresh `async_login()` call when they receive HTTP 401/403. An `asyncio.Lock` on the client ensures that when both coordinators hit a 401 at the same time, only one re-login occurs. If the retry also fails, a `DhlApiError` propagates up to the coordinator, which raises `UpdateFailed`, and HA marks the integration as unavailable until the next poll.

## hass.data structure

```python
hass.data["dhl_nl"] = {
    "<entry_id>": {
        "client": DhlApiClient,           # shared API client
        "coordinator": DhlCoordinator,    # incoming parcels coordinator
        "sent_coordinator": DhlSentShipmentsCoordinator,  # outgoing shipments coordinator
        "user_info": dict,                # login response: userId, email, firstName, lastName, locale
    }
}
```

## Sensor unique ID patterns

| Sensor class | Unique ID pattern | Example |
|---|---|---|
| `DhlIncomingParcelsSensor` | `{userId}_incoming_parcels` | `abc123_incoming_parcels` |
| `DhlParcelSensor` | `{userId}_{barcode}` | `abc123_JUN491599949120274226025` |
| `DhlNextDeliverySensor` | `{userId}_next_delivery` | `abc123_next_delivery` |
| `DhlEnRouteToServicePointSensor` | `{userId}_en_route_to_service_point` | `abc123_en_route_to_service_point` |
| `DhlPickupPendingSensor` | `{userId}_pickup_pending` | `abc123_pickup_pending` |
| `DhlDeliveredParcelsSensor` | `{userId}_delivered_parcels` | `abc123_delivered_parcels` |
| `DhlSentShipmentsSensor` | `{userId}_outgoing_parcels` | `abc123_outgoing_parcels` |
| `DhlOutgoingDeliveredSensor` | `{userId}_outgoing_delivered_parcels` | `abc123_outgoing_delivered_parcels` |

`userId` comes from the login response and is a UUID.

## Adding a new endpoint

1. Add the URL constant to `const.py`
2. Add an `async_get_*` method to `DhlApiClient` in `api.py`
3. Add a filter function and a new `DataUpdateCoordinator` subclass in `coordinator.py`
4. Instantiate the coordinator in `__init__.py` and store it in `hass.data`
5. Add the corresponding sensor class(es) in `sensor.py`
