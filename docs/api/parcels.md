# GET /receiver-parcel-api/parcels

Returns the list of parcels associated with the authenticated account. Includes incoming deliveries, returns, and historical shipments. The response is not paginated — all parcels are returned in a single call.

## Request

**URL:** `https://my.dhlecommerce.nl/receiver-parcel-api/parcels`  
**Method:** `GET`

### Headers

| Header | Value | Description |
|--------|-------|-------------|
| `x-xsrf-token` | `<XSRF-TOKEN cookie value>` | Required. Read from the `XSRF-TOKEN` cookie set during login. |

### Cookies

The `X-AUTH-TOKEN` and `XSRF-TOKEN` cookies set by the login endpoint must be present in the request.

## Response

**Status:** `200 OK` on success. `401`/`403` indicates an expired session — re-authenticate and retry.

### Body structure

```json
{
  "parcels": [ ... ],
  "postalCodeValidations": []
}
```

| Field | Type | Description |
|-------|------|-------------|
| `parcels` | array | List of parcel objects (see below). |
| `postalCodeValidations` | array | Always observed as empty. Purpose unknown. |

### Parcel object

```json
{
  "parcelId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "status": "DELIVERED",
  "category": "DELIVERED",
  "destination": {
    "name": null,
    "address": {
      "countryCode": "NL",
      "city": "Amsterdam",
      "postalCode": "1234AB",
      "street": "Examplestreet",
      "houseNumber": "1",
      "houseNumberSuffix": "",
      "lines": null
    },
    "locationType": "ADDRESS",
    "code": null,
    "openingTimes": null,
    "closurePeriods": null,
    "latitude": null,
    "longitude": null,
    "servicePointFormat": null
  },
  "receiver": {
    "name": "J. Doe",
    "address": {
      "countryCode": "NL",
      "city": "Amsterdam",
      "postalCode": "1234AB",
      "street": "Examplestreet",
      "houseNumber": "1",
      "houseNumberSuffix": "",
      "lines": null
    },
    "email": "user@example.com",
    "phone": null,
    "fax": null,
    "contactName": null
  },
  "sender": {
    "name": "Example Sender BV",
    "address": null,
    "email": null,
    "phone": null,
    "fax": null,
    "contactName": null
  },
  "origin": null,
  "receivingTimeIndication": {
    "moment": "2026-05-07T10:19:48Z",
    "indicationType": "MomentIndication"
  },
  "barcode": "JXXXXXXXXXXXXXXXXX",
  "intervenable": false,
  "isRegistered": false,
  "isReturnedToShipper": false,
  "isReturn": false,
  "receivedDaysAgo": 7,
  "returnedDaysAgo": null,
  "isClimateNeutralDelivery": false,
  "undeliverableSinceDaysAgo": null,
  "returnable": true,
  "hasDeliveryCode": false,
  "hasServicePointPickupCode": false,
  "hasLockerCode": false,
  "orderId": null,
  "createdAt": "2026-05-06T19:44:18.631297Z"
}
```

### Parcel fields

| Field | Type | Description |
|-------|------|-------------|
| `parcelId` | string (UUID) | Unique identifier for this parcel record. |
| `status` | string | Granular status string (see [Status values](#status-values)). Used as the sensor state for per-parcel sensors. |
| `category` | string | Coarser grouping used for filtering (see [Categories](#categories)). The integration uses this field to determine whether a parcel is active. |
| `destination` | object | Where the parcel is being delivered to. See [Location object](#location-object). |
| `receiver` | object | Recipient contact details. See [Contact object](#contact-object). |
| `sender` | object | Sender contact details. See [Contact object](#contact-object). Often only `name` is populated. |
| `origin` | object\|null | The DHL ServicePoint where a return was dropped off. `null` for regular deliveries. See [Location object](#location-object). |
| `receivingTimeIndication` | object | Estimated or actual delivery moment. `moment` is an ISO 8601 timestamp; `indicationType` is always `"MomentIndication"`. |
| `barcode` | string | Shipment tracking barcode. Used as the unique identifier for per-parcel sensors. |
| `intervenable` | boolean | Whether the delivery can still be redirected or modified. |
| `isRegistered` | boolean | Whether the parcel is registered in the user's account. |
| `isReturnedToShipper` | boolean | `true` if the parcel was returned to the original sender. |
| `isReturn` | boolean | `true` if this is a return shipment (sent back by the account holder). The integration excludes returns from the incoming parcels sensor. |
| `receivedDaysAgo` | integer\|null | Days since delivery. `null` if not yet delivered. |
| `returnedDaysAgo` | integer\|null | Days since the return was completed. `null` if not a return or not yet returned. |
| `isClimateNeutralDelivery` | boolean | Whether the delivery was carbon-neutral. |
| `undeliverableSinceDaysAgo` | integer\|null | Days since the parcel became undeliverable. `null` in most cases. |
| `returnable` | boolean | Whether the parcel can still be returned via the DHL portal. |
| `hasDeliveryCode` | boolean | Whether a delivery code is required to receive the parcel. |
| `hasServicePointPickupCode` | boolean | Whether a pickup code is needed to collect from a ServicePoint. |
| `hasLockerCode` | boolean | Whether a locker code is available for collection. |
| `orderId` | string\|null | Associated order ID. Usually `null`. |
| `createdAt` | string (ISO 8601) | When the parcel record was created in the DHL system. |

### Location object

Used for `destination` and `origin`.

| Field | Type | Description |
|-------|------|-------------|
| `name` | string\|null | Name of the location (e.g. ServicePoint name). `null` for home addresses. |
| `address` | object\|null | Address details: `countryCode`, `city`, `postalCode`, `street`, `houseNumber`, `houseNumberSuffix`, `lines`. |
| `locationType` | string | One of `ADDRESS`, `SERVICEPOINT`, `RETURN`. |
| `code` | string\|null | ServicePoint code (e.g. `"NL-571106"`). `null` for non-ServicePoint locations. |
| `openingTimes` | array\|null | Array of `{ timeFrom, timeTo, weekDay }` objects (weekDay: 1=Monday). Only present for ServicePoints. |
| `closurePeriods` | array\|null | Array of closure periods. Usually empty. |
| `latitude` | number\|null | GPS latitude. Only present for ServicePoints. |
| `longitude` | number\|null | GPS longitude. Only present for ServicePoints. |
| `servicePointFormat` | string\|null | Type of ServicePoint, e.g. `"Shop"`. |

### Contact object

Used for `receiver` and `sender`.

| Field | Type | Description |
|-------|------|-------------|
| `name` | string\|null | Full name or company name. |
| `address` | object\|null | Same structure as the address in Location object. |
| `email` | string\|null | Email address. May be a marketplace-generated proxy address. |
| `phone` | string\|null | Phone number. Format varies. |
| `fax` | string\|null | Fax number. Always `null` in observed data. |
| `contactName` | string\|null | Contact person name. Always `null` in observed data. |

## Status values

The `status` field is more granular than `category`. Observed values:

| Status | Description |
|--------|-------------|
| `DELIVERED` | Delivered at the door |
| `DELIVERED_IN_MAILBOX` | Delivered in the mailbox |
| `COLLECTED_AT_PARCELSHOP` | Collected by the recipient at a ServicePoint |
| `RETURN_DELIVERED_AT_SHIPPER_CALCULATED` | Return shipment delivered back to the original sender |

Additional in-transit status values are expected but not yet observed in this dataset — see the `category` field for the active state groupings used by the integration.

## Categories

The `category` field is used by the integration to determine whether a parcel is active. See [`const.py`](../../custom_components/dhl_nl/const.py) for the full `ACTIVE_CATEGORIES` set.

| Category | Description |
|----------|-------------|
| `DELIVERED` | Terminal state — parcel is no longer tracked by the integration |
| `CUSTOMS` | Being processed by customs |
| `DATA_RECEIVED` | Shipment registered / label created |
| `EXCEPTION` | Something went wrong, delay expected |
| `IN_DELIVERY` | Parcel is in transit |
| `INTERVENTION` | An intervention occurred in the delivery process |
| `LEG` | Domestic leg registered (early trace event) |
| `PROBLEM` | Same as `EXCEPTION` |
| `UNDERWAY` | Parcel is being sorted |
| `UNKNOWN` | Status unknown |

## Filtering applied by the integration

The integration applies two filters before exposing parcels as sensors:

1. `isReturn` must be `false` — return shipments are excluded from the incoming parcels sensor
2. `category` must be in `ACTIVE_CATEGORIES` — parcels with `DELIVERED` category are excluded

## Error handling

| Status | Meaning |
|--------|---------|
| `200` | Success |
| `401` / `403` | Session expired — the integration re-authenticates and retries once |
| `4xx` / `5xx` | Other failure — raises `DhlApiError` with the status code |
