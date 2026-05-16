# DHL NL Package Tracker

A custom Home Assistant integration that tracks your incoming DHL eCommerce NL parcels. It polls the DHL eCommerce NL API every 30 minutes and exposes a summary sensor plus one sensor per active shipment.

## Features

- **Summary sensor** — shows the total number of active parcels in transit
- **Per-parcel sensors** — one sensor per active shipment, with full parcel details as attributes
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

### `sensor.<account>_dhl_packages`

Summary sensor for the configured account. The `<account>` prefix is derived from your email address (e.g. `fam_example_nl` for `fam@example.nl`).

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of all active parcel objects returned by the API |

**State:** number of active parcels (unit: `packages`)

### `sensor.<account>_dhl_parcel_<barcode>`

One sensor per active shipment.

**State:** parcel status string (e.g. `IN_DELIVERY`, `UNDERWAY`)

**Attributes:** all fields returned by the DHL API for that parcel, including barcode, estimated delivery, address, and event history.

## Active parcel categories

Only parcels in the following categories are tracked (returns are excluded):

`PROBLEM` · `CUSTOMS` · `DATA_RECEIVED` · `EXCEPTION` · `INTERVENTION` · `IN_DELIVERY` · `LEG` · `UNDERWAY` · `UNKNOWN`

Parcels with a `DELIVERED` or similar terminal status are automatically removed.

## Poll interval

Data is refreshed every **30 minutes**. You can trigger a manual refresh from the integration's device page using the **Reload** option.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `invalid_auth` error during setup | Wrong email or password |
| `cannot_connect` error during setup | DHL API is unreachable; check your network |
| Sensors disappear after delivery | Expected — delivered parcels are filtered out |
| Sensors not updating | Check **Settings → System → Logs** for `dhl` entries |

Enable debug logging by adding the following to `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.dhl_nl: debug
```

## Contributing

Pull requests and issues are welcome. Please open an issue before submitting a large change.

## License

MIT
