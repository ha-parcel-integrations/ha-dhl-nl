# Sensors

Full reference for all sensors provided by the DHL NL integration.

## Incoming parcels

### `sensor.<account>_dhl_incoming_parcels`

Summary sensor showing how many parcels are currently on their way to you.

**State:** number of active incoming parcels (unit: `parcels`)

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of all active incoming parcel objects returned by the API |

### `sensor.<account>_dhl_parcel_<barcode>`

One sensor per active incoming shipment. Created automatically when a new parcel appears and removed once it is delivered.

**State:** parcel status string (e.g. `IN_DELIVERY`, `UNDERWAY`)

**Attributes:** all fields returned by the DHL API for that parcel, including barcode, estimated delivery, address, and event history.

### `sensor.<account>_dhl_next_delivery`

Earliest expected delivery datetime across all active incoming parcels. Uses device class `timestamp` so Home Assistant treats it as a proper datetime — useful for time-based automations.

**State:** datetime of the next expected delivery, or unavailable if no parcels have a known delivery time

| Attribute | Description |
|-----------|-------------|
| `barcode` | Barcode of the parcel arriving soonest |
| `sender` | Name of the sender of that parcel |

**Example automation:** notify 1 hour before the next delivery:

```yaml
trigger:
  - platform: template
    value_template: >
      {{ (as_timestamp(states('sensor.dhl_next_delivery')) - as_timestamp(now())) < 3600 }}
```

### `sensor.<account>_dhl_en_route_to_service_point`

Parcels still in transit to a DHL ServicePoint (status is not yet `NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT`).

**State:** number of parcels en route to a ServicePoint (unit: `parcels`)

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of en-route parcels, each with `barcode`, `sender`, `service_point`, `service_point_address`, and `status` |

### `sensor.<account>_dhl_parcels_awaiting_pickup`

Parcels that have arrived at a DHL ServicePoint and are ready to be collected (status is `NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT`).

**State:** number of parcels awaiting pickup (unit: `parcels`)

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of pending pickup parcels, each with `barcode`, `sender`, `pickup_location`, `pickup_address`, and `status` |

**Example automation:** send a notification when a parcel is ready for pickup:

```yaml
trigger:
  - platform: numeric_state
    entity_id: sensor.dhl_parcels_awaiting_pickup
    above: 0
action:
  - service: notify.mobile_app
    data:
      message: "You have a parcel waiting at a DHL ServicePoint."
```

### `sensor.<account>_dhl_delivered_parcels`

Recently delivered incoming parcels. The number of parcels shown is controlled by the integration options (see [Configuration](#configuration)).

**State:** number of delivered parcels shown (unit: `parcels`)

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of delivered parcels, each with `barcode`, `sender`, `status`, and `delivery_date` |

---

## Configuration

After the initial setup you can configure the delivered parcels filter via **Settings → Devices & Services → DHL → Configure**:

| Option | Description |
|--------|-------------|
| **Filter by** | `Days` — show parcels delivered in the last N days. `Number of parcels` — show the N most recent deliveries. |
| **Amount** | The number of days or parcels (1–365). Default: **7 days**. |

Changes take effect on the next data refresh without requiring a restart.

---

## Outgoing shipments

### `sensor.<account>_dhl_outgoing_parcels`

Summary sensor showing how many packages you have sent that are still in transit. Shipments with a `DELIVERED` status are automatically excluded. No per-shipment sensors are created — all data is available as attributes on this single sensor.

**State:** number of active outgoing shipments (unit: `parcels`)

| Attribute | Description |
|-----------|-------------|
| `shipments` | List of active outgoing shipment objects, each containing `barcode`, `orderId`, `status`, `category`, `receiver`, `destination`, `timeCreated`, and `receivingTimeIndication` |

---

## Active shipment categories

Both incoming and outgoing sensors only track shipments in the following categories. `DELIVERED` is the only terminal state and is always excluded.

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

Data is refreshed every **30 minutes**. You can trigger a manual refresh from the integration's device page using the **Reload** option.

---

## Debug logging

Add the following to `configuration.yaml` to enable verbose logging:

```yaml
logger:
  logs:
    custom_components.dhl_nl: debug
```
