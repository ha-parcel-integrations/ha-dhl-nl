# Sensors

Full reference for all sensors provided by the DHL NL integration.

> **Friendly name pattern:** the integration creates one device per
> DHL account, named `DHL (<your-email>)`. Each sensor's friendly name
> is `<device-name> <entity-name>`, e.g.
> `DHL (account@example.com) Incoming parcels`.

> **Parcel shape:** every parcel exposed on a sensor attribute carries
> the carrier-agnostic top-level keys `carrier`, `barcode`, `sender`,
> `receiver`, `status` (the normalised
> [`ParcelStatus`](#parcel-status-reference) value), `raw_status` (the
> original DHL string), `delivered`, `delivered_at`, `planned_from`,
> `planned_to`, `pickup`, `pickup_point`, `url`, `weight` (kg, always
> `null` for DHL — not exposed by the API), `dimensions`
> (`{length, width, height, text}` in cm with `text` a pre-formatted
> `"L x W x H cm"` string, always `null` for DHL), plus the original
> DHL payload under `raw`.

## Incoming parcels

### `DHL (account) Incoming parcels`

Summary sensor showing how many parcels are currently on their way to you.

**State:** number of active incoming parcels (unit: `parcels`, translated as `pakketten` in Dutch HA).

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of all active incoming parcel objects (normalised carrier-agnostic shape). Not recorded long-term to keep the recorder DB lean. |

### `DHL (account) Parcel <barcode>`

One sensor per active incoming shipment. Created automatically when a new parcel appears and removed once it is delivered.

**State:** the normalised [`ParcelStatus`](#parcel-status-reference)
value (e.g. `out_for_delivery`, `at_pickup_point`).

**Attributes:** the full normalised parcel dict — top-level fields plus
`raw_status` (the original DHL string) and `raw` (the full DHL payload).

### `DHL (account) Next delivery`

Earliest expected delivery datetime across all active incoming parcels.
Uses device class `timestamp` so Home Assistant treats it as a proper
datetime — useful for time-based automations.

**State:** datetime of the next expected delivery, or `unavailable` if
no parcels have a known delivery time.

| Attribute | Description |
|-----------|-------------|
| `barcode` | Barcode of the parcel arriving soonest |
| `sender` | Name of the sender of that parcel |
| `receiver` | Recipient name of that parcel (from DHL's `receiver.name` field) |

### `DHL (account) En route to ServicePoint`

Parcels destined for a DHL ServicePoint that have *not yet arrived*
(`status != at_pickup_point`).

**State:** number of parcels en route to a ServicePoint (unit: `parcels`).

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of normalised en-route parcels |

### `DHL (account) Awaiting pickup`

Parcels that have arrived at a DHL ServicePoint and are ready to be
collected (`status == at_pickup_point`).

**State:** number of parcels awaiting pickup (unit: `parcels`).

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of normalised parcels awaiting pickup |

### `DHL (account) Delivered parcels`

Recently delivered incoming parcels. The window is controlled by the
integration options (see [Options](#options)).

**State:** number of delivered parcels shown (unit: `parcels`).

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of normalised delivered parcels |

---

## Outgoing shipments

### `DHL (account) Outgoing parcels`

Summary sensor showing how many packages you have sent that are still
in transit. Shipments with normalised `status == delivered` are
excluded. No per-shipment sensors are created — all data is available
as attributes on this single sensor.

**State:** number of active outgoing shipments (unit: `parcels`).

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of normalised active outgoing shipments |

---

## Parcel status reference

`status` on every parcel is one of these canonical
[`ParcelStatus`](../custom_components/dhl_nl/const.py) values. Use these
in automations rather than DHL's raw strings — `raw_status` keeps the
original DHL value available for power users.

| `status` | Meaning | DHL raw status / category that maps here |
|---|---|---|
| `registered` | DHL knows about the label but the parcel is not yet in transit | category `DATA_RECEIVED` or `LEG` |
| `in_transit` | Picked up; somewhere in DHL's network | category `UNDERWAY`, `IN_DELIVERY`, or `CUSTOMS` |
| `out_for_delivery` | On the delivery vehicle today | raw status `OUT_FOR_DELIVERY` |
| `at_pickup_point` | Arrived at the chosen ServicePoint, ready to be collected | raw status `NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT` |
| `delivered` | Handed over, dropped in mailbox, or picked up | category `DELIVERED` or raw status `COLLECTED_AT_PARCELSHOP` |
| `returning` | Failed delivery, on the way back | (not yet observed; will be added once the raw indicator is confirmed) |
| `problem` | Carrier reports an exception, intervention, or other issue | category `INTERVENTION`, `EXCEPTION`, or `PROBLEM` |
| `unknown` | Raw status/category we have not mapped yet | anything else — logged once at info level |

---

## Events

The coordinator fires events on the HA event bus when something changes:

| Event | When | Payload |
|---|---|---|
| `dhl_nl_parcel_registered` | A new barcode appears in the active list | Full normalised parcel dict |
| `dhl_nl_parcel_status_changed` | A known barcode's normalised `status` changes | Normalised parcel dict plus `old_status` and `new_status` |
| `dhl_nl_parcel_delivery_time_changed` | A known barcode's `planned_from` or `planned_to` ends up with a non-null value different from the previous one. Value-to-null transitions are silent. | Normalised parcel dict plus `old_planned_from`, `new_planned_from`, `old_planned_to`, `new_planned_to` |

Events are suppressed on the very first refresh after start-up to avoid
a flood of "registered" events for parcels that already existed.

See [`examples/automations/`](../examples/automations/) for ready-to-paste
event-driven automations.

---

## Options

After setup, click **Configure** on the integration card. The form is
split into two sections:

### Delivered parcels

| Option | Description |
|--------|-------------|
| **Filter by** | `Days` — show parcels delivered in the last N days. `Number of parcels` — show the N most recent deliveries. |
| **Amount** | The number of days or parcels (1–365). Default: **7 days**. |

### Polling

| Option | Description |
|--------|-------------|
| **Refresh every** | How often the integration checks DHL. Choices: **15 / 30 / 60 / 120 / 240 minutes** — default 30. A slower interval is gentler on DHL's consumer API. Changes take effect immediately, no HA restart needed. |

---

## Active shipment categories

Both incoming and outgoing sensors only track shipments in the following
categories. `DELIVERED` is the only terminal category and is always
excluded from the *active* lists (it lands on the delivered sensor
instead).

| Category | Description |
|----------|-------------|
| `CUSTOMS` | Being processed by customs |
| `DATA_RECEIVED` | Shipment registered / label created |
| `EXCEPTION` | Something went wrong, delay expected |
| `IN_DELIVERY` | Parcel is in transit |
| `INTERVENTION` | An intervention occurred in the delivery process |
| `LEG` | Domestic leg registered (early trace event) |
| `PROBLEM` | Same as `EXCEPTION` |
| `UNDERWAY` | Parcel is being sorted |
| `UNKNOWN` | Status unknown |

---

## Poll interval

Default cadence is **30 minutes**, configurable via the **Polling**
section of the integration's Options (see above). You can also trigger
a manual refresh from the integration's device page using the **Reload**
option.

---

## Debug logging

Add the following to `configuration.yaml` to enable verbose logging:

```yaml
logger:
  logs:
    custom_components.dhl_nl: debug
```
