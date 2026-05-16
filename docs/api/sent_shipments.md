# GET /api/orders/sentShipments

Returns the list of outgoing shipments sent by the authenticated account. Includes both active and delivered shipments. The `max` query parameter controls how many records are returned.

## Request

**URL:** `https://my.dhlecommerce.nl/api/orders/sentShipments?max=250`  
**Method:** `GET`

### Query parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `max` | integer | Maximum number of shipments to return. The integration uses `250`. |

### Headers

| Header | Value | Description |
|--------|-------|-------------|
| `x-xsrf-token` | `<XSRF-TOKEN cookie value>` | Required. Read from the `XSRF-TOKEN` cookie set during login. |

### Cookies

The `X-AUTH-TOKEN` and `XSRF-TOKEN` cookies set by the login endpoint must be present in the request.

## Response

**Status:** `200 OK` on success. `401`/`403` indicates an expired session — re-authenticate and retry.

### Body structure

Unlike the parcels endpoint, this endpoint returns a **JSON array** directly (not a wrapped object).

```json
[ ... ]
```

### Shipment object

```json
{
  "type": "outgoing",
  "orderId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "barcode": "3SXXXXXXXXXXXXXXXXX",
  "receiver": {
    "name": "J. Doe",
    "locationType": "ADDRESS",
    "address": {
      "street": "Examplestreet",
      "houseNumber": "1",
      "postalCode": "1234AB",
      "city": "Amsterdam",
      "country": "NL"
    }
  },
  "destination": {
    "name": "Example ServicePoint",
    "locationType": "PARCEL_SHOP",
    "address": {
      "street": "Servicestreet",
      "houseNumber": "10",
      "postalCode": "5678CD",
      "city": "Rotterdam",
      "country": "NL"
    }
  },
  "sender": {
    "name": "J. Doe",
    "locationType": "UNKNOWN",
    "address": {
      "street": "Examplestreet",
      "houseNumber": "1",
      "postalCode": "1234AB",
      "city": "Amsterdam",
      "country": "NL"
    }
  },
  "timeCreated": "2025-05-29T15:36:16+0000",
  "receivingTimeIndication": {
    "indicationType": "MomentIndication",
    "moment": null
  },
  "status": "COLLECTED_AT_PARCELSHOP",
  "category": "DELIVERED",
  "intervenable": false,
  "totalPrice": {
    "withoutTax": 5.33,
    "withTax": 6.45,
    "currency": "EUR"
  }
}
```

### Shipment fields

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Always `"outgoing"` in observed data. The integration filters on this field. |
| `orderId` | string (UUID) | Unique identifier for the order in the DHL system. |
| `barcode` | string | Shipment tracking barcode. |
| `receiver` | object | The recipient of the shipment. See [Party object](#party-object). |
| `destination` | object | The physical delivery location (may differ from receiver address when delivering to a ServicePoint). See [Party object](#party-object). |
| `sender` | object | The account holder who sent the shipment. See [Party object](#party-object). `locationType` is often `"UNKNOWN"`. |
| `timeCreated` | string (ISO 8601) | When the shipment order was created. |
| `receivingTimeIndication` | object | Estimated delivery moment. `moment` is an ISO 8601 timestamp or `null`; `indicationType` is always `"MomentIndication"`. |
| `status` | string | Granular status string (see [Status values](#status-values)). |
| `category` | string | Coarser grouping used for filtering (see [Categories](#categories)). The integration uses this field to determine whether a shipment is still active. |
| `intervenable` | boolean | Whether the delivery can still be redirected or modified. |
| `totalPrice` | object | Shipping cost: `withoutTax`, `withTax` (both numbers), and `currency` (string, e.g. `"EUR"`). |

### Party object

Used for `receiver`, `destination`, and `sender`.

| Field | Type | Description |
|-------|------|-------------|
| `name` | string\|null | Full name, company name, or ServicePoint name. |
| `locationType` | string | One of `ADDRESS`, `PARCEL_SHOP`, `UNKNOWN`. |
| `address` | object\|null | Address details: `street`, `houseNumber`, `postalCode`, `city`, `country`. Note: uses shorter field names than the parcels endpoint. |

## Status values

The `status` field is more granular than `category`. Observed values:

| Status | Description |
|--------|-------------|
| `COLLECTED_AT_PARCELSHOP` | Shipment has been collected at the drop-off ServicePoint |

Additional in-transit status values are expected but not yet observed in this dataset — see the `category` field for the active state groupings used by the integration.

## Categories

The `category` field is used by the integration to determine whether a shipment is still active. The same `ACTIVE_CATEGORIES` set as the parcels endpoint applies. See [`const.py`](../../custom_components/dhl_nl/const.py).

| Category | Description |
|----------|-------------|
| `DELIVERED` | Terminal state — shipment is no longer tracked by the integration |
| `CUSTOMS` | Being processed by customs |
| `DATA_RECEIVED` | Shipment registered / label created |
| `EXCEPTION` | Something went wrong, delay expected |
| `IN_DELIVERY` | Shipment is in transit |
| `INTERVENTION` | An intervention occurred in the delivery process |
| `LEG` | Domestic leg registered (early trace event) |
| `PROBLEM` | Same as `EXCEPTION` |
| `UNDERWAY` | Shipment is being sorted |
| `UNKNOWN` | Status unknown |

## Filtering applied by the integration

The integration applies two filters before exposing shipments in the sensor:

1. `type` must be `"outgoing"` — only outgoing shipments are included
2. `category` must be in `ACTIVE_CATEGORIES` — shipments with `DELIVERED` category are excluded

## Differences from the parcels endpoint

| Aspect | Parcels endpoint | Sent shipments endpoint |
|--------|-----------------|------------------------|
| Response wrapper | `{ "parcels": [...] }` | Raw JSON array `[...]` |
| Address field names | `countryCode`, `houseNumberSuffix`, `lines` | `country` (no suffix or lines fields) |
| Price information | Not present | `totalPrice` object included |
| Return filtering | `isReturn` field | `type` field |

## Error handling

| Status | Meaning |
|--------|---------|
| `200` | Success |
| `401` / `403` | Session expired — the integration re-authenticates and retries once |
| `4xx` / `5xx` | Other failure — raises `DhlApiError` with the status code |
