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

docs/api/
├── README.md          # API overview and authentication
├── login.md           # POST /api/user/login
├── parcels.md         # GET /receiver-parcel-api/parcels
└── sent_shipments.md  # GET /api/orders/sentShipments
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
  polls every 30 min, filters active parcels/shipments
        │                    │
        ▼                    ▼
  DhlPackagesSensor       DhlSentShipmentsSensor
  DhlParcelSensor         (sensor.py)
  DhlNextDeliverySensor
  DhlPickupPendingSensor
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
- `DhlCoordinator` — polls `async_get_parcels()`, applies `filter_active_parcels()` (excludes returns and non-active categories)
- `DhlSentShipmentsCoordinator` — polls `async_get_sent_shipments()`, applies `filter_active_sent_shipments()` (keeps only `type == "outgoing"` and active categories)
- Both coordinators raise `UpdateFailed` on any `DhlApiError` or `aiohttp.ClientError`; session recovery is handled by the client, not the coordinators

### `sensor.py`
- `DhlPackagesSensor` — summary sensor for incoming parcels; also manages the lifecycle of `DhlParcelSensor` entities (creates new ones, removes stale ones from the entity registry on each coordinator update)
- `DhlParcelSensor` — one entity per active incoming parcel, keyed by barcode
- `DhlNextDeliverySensor` — derives the earliest `receivingTimeIndication.moment` across all active parcels; device class `TIMESTAMP` for native HA datetime handling
- `DhlPickupPendingSensor` — counts active parcels where `destination.locationType == "SERVICEPOINT"` and status is not `COLLECTED_AT_PARCELSHOP`
- `DhlSentShipmentsSensor` — single summary sensor for outgoing shipments; no per-shipment entities are created

## Key design decisions

### Two coordinators, one client
Both coordinators share a single `DhlApiClient` instance. This means they share the same `aiohttp` session and cookie jar, so a re-authentication by one coordinator also refreshes the session for the other.

### Dynamic per-parcel sensor lifecycle
`DhlPackagesSensor` tracks a `_known_barcodes` set. On each coordinator update it diffs the current barcodes against the known set, calls `async_add_entities` for new ones, and removes stale ones from the entity registry. This means parcel sensors appear and disappear automatically without requiring an HA restart.

### No per-shipment sensors for outgoing
Outgoing shipments are exposed as a single sensor with a list attribute. This is intentional — outgoing shipments are less frequently monitored and the data is fully accessible via the attribute. Adding per-shipment sensors would follow the same pattern as `DhlParcelSensor` if needed in the future.

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
| `DhlPickupPendingSensor` | `{userId}_pickup_pending` | `abc123_pickup_pending` |
| `DhlSentShipmentsSensor` | `{userId}_outgoing_parcels` | `abc123_outgoing_parcels` |

`userId` comes from the login response and is a UUID.

## Adding a new endpoint

1. Add the URL constant to `const.py`
2. Add an `async_get_*` method to `DhlApiClient` in `api.py`
3. Add a filter function and a new `DataUpdateCoordinator` subclass in `coordinator.py`
4. Instantiate the coordinator in `__init__.py` and store it in `hass.data`
5. Add the corresponding sensor class(es) in `sensor.py`
6. Document the endpoint in `docs/api/`
