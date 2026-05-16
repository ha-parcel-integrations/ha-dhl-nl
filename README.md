# DHL NL Parcel Tracker

A custom Home Assistant integration that tracks your incoming and outgoing DHL eCommerce NL shipments. It polls the DHL eCommerce NL API every 30 minutes and exposes sensors for both parcels on their way to you and parcels you have sent.

## Features

- **Incoming summary sensor** — shows the total number of active parcels on their way to you
- **Per-parcel sensors** — one sensor per active incoming shipment, with full parcel details as attributes
- **Next delivery sensor** — shows the earliest expected delivery datetime across all active parcels
- **Parcels awaiting pickup sensor** — shows how many parcels are waiting at a ServicePoint for collection
- **Outgoing summary sensor** — shows the total number of sent shipments still in transit
- **Automatic lifecycle management** — new parcel sensors are created when shipments appear; stale sensors are removed when shipments are delivered or no longer active
- **Session recovery** — automatically re-authenticates when the session expires (HTTP 401/403)
- **Re-authentication flow** — supports HA's built-in re-auth UI when credentials change

## Requirements

- Home Assistant 2024.1 or newer
- A [DHL eCommerce NL](https://my.dhlecommerce.nl) account (the consumer portal, not the business API)

## Installation

### HACS (recommended)

1. Open HACS → **Integrations** → ⋮ → **Custom repositories**
2. Add this repository URL and select category **Integration**
3. Search for **DHL** and install it
4. Restart Home Assistant

### Manual

1. Copy the `dhl_nl` folder into your `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **DHL**
3. Enter your DHL eCommerce NL **email address** and **password**
4. Click **Submit**

The integration validates your credentials against the DHL API before saving. If login fails you will see an error message in the form.

## Sensors

### Incoming parcels

#### `sensor.<account>_dhl_incoming_parcels`

Summary sensor showing how many parcels are currently on their way to you.

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of all active incoming parcel objects returned by the API |

**State:** number of active incoming parcels (unit: `packages`)

#### `sensor.<account>_dhl_parcel_<barcode>`

One sensor per active incoming shipment. Created automatically when a new parcel appears and removed once it is delivered.

**State:** parcel status string (e.g. `IN_DELIVERY`, `UNDERWAY`)

**Attributes:** all fields returned by the DHL API for that parcel, including barcode, estimated delivery, address, and event history.

#### `sensor.<account>_dhl_next_delivery`

Shows the earliest expected delivery datetime across all active incoming parcels. Uses device class `timestamp` so Home Assistant treats it as a proper datetime — useful for time-based automations.

| Attribute | Description |
|-----------|-------------|
| `barcode` | Barcode of the parcel arriving soonest |
| `sender` | Name of the sender of that parcel |

**State:** datetime of the next expected delivery, or unavailable if no parcels have a known delivery time

**Example automation:** notify 1 hour before the next delivery:
```yaml
trigger:
  - platform: template
    value_template: >
      {{ (as_timestamp(states('sensor.dhl_next_delivery')) - as_timestamp(now())) < 3600 }}
```

#### `sensor.<account>_dhl_parcels_awaiting_pickup`

Shows how many parcels are waiting at a DHL ServicePoint to be collected. A parcel is counted when its destination is a ServicePoint and it has not yet been collected (`COLLECTED_AT_PARCELSHOP`).

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of pending pickup parcels, each with `barcode`, `sender`, `pickup_location`, `pickup_address`, and `status` |

**State:** number of parcels awaiting pickup (unit: `packages`)

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

### Outgoing shipments

#### `sensor.<account>_dhl_outgoing_parcels`

Summary sensor showing how many packages you have sent that are still in transit.

| Attribute | Description |
|-----------|-------------|
| `shipments` | List of active outgoing shipment objects, each containing `barcode`, `orderId`, `status`, `category`, `receiver`, `destination`, `timeCreated`, and `receivingTimeIndication` |

**State:** number of active outgoing shipments (unit: `packages`)

Shipments with a `DELIVERED` status are automatically excluded. No per-shipment sensors are created — all data is available as attributes on this single sensor.

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

## Poll interval

Data is refreshed every **30 minutes**. You can trigger a manual refresh from the integration's device page using the **Reload** option.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `invalid_auth` error during setup | Wrong email or password |
| `cannot_connect` error during setup | DHL API is unreachable; check your network |
| Sensors disappear after delivery | Expected — delivered shipments are filtered out |
| Sensors not updating | Check **Settings → System → Logs** for `dhl` entries |

Enable debug logging by adding the following to `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.dhl_nl: debug
```

## Disclaimer

This integration and its documentation were generated with the assistance of AI tools. It is an independent, community-built project with no affiliation, endorsement, or connection to DHL or any of its subsidiaries.

Use at your own risk. The DHL eCommerce NL API is undocumented and may change without notice, which could break this integration at any time.

## Contributing

Pull requests and issues are welcome. Please open an issue before submitting a large change.

## License

MIT
