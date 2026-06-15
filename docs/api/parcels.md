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
| `receivingTimeIndication` | object\|null | Estimated delivery time. Structure depends on `indicationType` — see [Receiving time indication](#receiving-time-indication). |
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

### Receiving time indication

The `receivingTimeIndication` field can be `null` or one of two structures depending on `indicationType`:

**`MomentIndication`** — a single point in time:
```json
{
  "moment": "2026-05-07T10:19:48Z",
  "indicationType": "MomentIndication"
}
```

**`IntervalIndication`** — a delivery window:
```json
{
  "start": "2026-05-20T08:00:00Z",
  "end": "2026-05-20T16:00:00Z",
  "indicationType": "IntervalIndication"
}
```

The integration uses `moment` for `MomentIndication` and `start` for `IntervalIndication` when computing the next delivery datetime.

## Status values

The `status` field is more granular than `category`. Observed values:

| Status | Description |
|--------|-------------|
| `DELIVERED` | Delivered at the door |
| `DELIVERED_IN_MAILBOX` | Delivered in the mailbox |
| `NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT` | Parcel has arrived at a DHL ServicePoint and the recipient has been notified for collection |
| `COLLECTED_AT_PARCELSHOP` | Collected by the recipient at a ServicePoint |
| `RETURN_DELIVERED_AT_SHIPPER_CALCULATED` | Return shipment delivered back to the original sender |

## Categories

The `category` field is used by the integration to determine whether a parcel is active. See [`const.py`](../../custom_components/dhl_nl/const.py) for the full `ACTIVE_CATEGORIES` set.

| Category | Description |
|----------|-------------|
| `CUSTOMS` | The shipment is being processed by customs |
| `DATA_RECEIVED` | The shipment is registered |
| `DELIVERED` | Delivered at door **or** DHL ServicePoint (see note below) |
| `EXCEPTION` | Something in the process went wrong, delay is expected |
| `INTERVENTION` | An intervention occurred in the delivery process |
| `IN_DELIVERY` | The parcel is in transit |
| `LEG` | Usually the start of a domestic delivery trace — shipment is registered |
| `PROBLEM` | Same as `EXCEPTION` |
| `UNDERWAY` | The parcel is being sorted |
| `UNKNOWN` | The status of the parcel is unknown |

> **ServicePoint lifecycle (confirmed from live data):**
> 1. In transit to ServicePoint → category `IN_DELIVERY`, various statuses
> 2. Arrived, recipient notified → category `IN_DELIVERY`, status `NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT`
> 3. Collected → category `DELIVERED`, status `COLLECTED_AT_PARCELSHOP` — filtered out by the coordinator
>
> Despite DHL's documentation stating `DELIVERED` covers "door or ServicePoint", state 2 above (available at ServicePoint) retains category `IN_DELIVERY`. Only after collection does the category become `DELIVERED`.

## Filtering applied by the integration

The integration applies two filters before exposing parcels as sensors:

1. `isReturn` must be `false` — return shipments are excluded from the incoming parcels sensor
2. `category` must be in `ACTIVE_CATEGORIES` — parcels with `DELIVERED` category are excluded

> **Note:** Parcels at a ServicePoint retain category `IN_DELIVERY` and are therefore included in the active set. The integration uses status `NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT` to distinguish arrived-at-ServicePoint parcels from those still in transit.

## Error handling

| Status | Meaning |
|--------|---------|
| `200` | Success |
| `401` / `403` | Session expired — the integration re-authenticates and retries once |
| `4xx` / `5xx` | Other failure — raises `DhlApiError` with the status code |

## How the integration exposes parcels

Each surviving parcel is transformed into a carrier-agnostic dict before being placed on a sensor attribute. Top-level keys come from the [shared shape](https://github.com/peternijssen/ha-parcel-aggregator); the original DHL payload is preserved under `raw`.

| Sensor field | Source on the DHL parcel |
|--------------|--------------------------|
| `carrier` | Constant `"DHL"` |
| `barcode` | `barcode` |
| `sender` | `sender.name` |
| `status` | `status` |
| `delivered` | `category == "DELIVERED"` |
| `delivered_at` | `receivingTimeIndication.moment` / `.start` (delivered only) |
| `planned_from` | `receivingTimeIndication.moment` for `MomentIndication`, `.start` for `IntervalIndication` (active only) |
| `planned_to` | `receivingTimeIndication.end` for `IntervalIndication`; `null` for `MomentIndication` |
| `pickup` | `destination.locationType == "SERVICEPOINT"` |
| `pickup_point` | `destination.name` when `pickup` is `true` |
| `url` | Constructed as `https://my.dhlecommerce.nl/portal/tracktrace/{barcode}/{destination.address.postalCode}` (whitespace stripped from the postcode). `null` when either is missing. |
| `raw` | The full parcel object as returned above |
