# DHL NL Parcel Tracker

A custom Home Assistant integration that tracks your incoming and outgoing DHL eCommerce NL shipments.

## Features

- Incoming and outgoing parcel count sensors
- Per-parcel sensor per active incoming shipment
- Next delivery datetime sensor (device class `timestamp`)
- ServicePoint sensors — en route and awaiting pickup
- Automatic lifecycle management — sensors are created and removed as parcels move through delivery
- Session recovery and re-authentication support

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

### Setup parameters

| Field | Description |
|---|---|
| Email | The email address of your DHL eCommerce NL account (the consumer portal at [my.dhlecommerce.nl](https://my.dhlecommerce.nl)). |
| Password | The password for that account. Stored in the HA config entry; updated automatically when the integration triggers a re-authentication. |

## Options

Click **Configure** on the integration card to change the delivered
parcels filter:

| Option | Description |
|---|---|
| Filter by | `Days` keeps delivered parcels visible for the last N days. `Number of parcels` keeps only the N most recent regardless of age. |
| Amount | The N used by the filter above. |

## Removal

Standard HA removal applies: **Settings → Devices & Services →
DHL NL → ⋮ → Delete**. No DHL-side cleanup is needed; deleting the
config entry stops the polling. To revoke API access entirely, change
your DHL account password — the integration will trigger a re-auth
notification, which you can then ignore.

## Sensors

The integration creates one device per DHL account, named
**`DHL (<your-email>)`**. With multiple accounts each gets its own device
named after its email. The entities below show the friendly-name pattern;
their entity_ids carry the same account suffix:

| Friendly name pattern | Description |
|---|---|
| `DHL (account) Incoming parcels` | Number of active incoming parcels |
| `DHL (account) Parcel <barcode>` | Normalised status of a single incoming shipment |
| `DHL (account) Next delivery` | Earliest expected delivery datetime |
| `DHL (account) En route to ServicePoint` | Parcels in transit to a ServicePoint |
| `DHL (account) Awaiting pickup` | Parcels ready for collection at a ServicePoint |
| `DHL (account) Delivered parcels` | Recently delivered parcels (configurable window) |
| `DHL (account) Outgoing parcels` | Number of active outgoing shipments |

Every parcel exposed on a sensor attribute uses a carrier-agnostic shape:

| Key | Type | Meaning |
|---|---|---|
| `carrier` | string | `"DHL"` |
| `barcode` | string | Parcel tracking number |
| `sender` | string \| null | Sender name (e.g. webshop) |
| `status` | `ParcelStatus` | Normalised status — see the [status reference](#parcel-status-reference) |
| `raw_status` | string | Original DHL status string (for power users) |
| `delivered` | bool | Whether the parcel has been delivered |
| `delivered_at` | ISO 8601 \| null | Delivery moment, if known |
| `planned_from` | ISO 8601 \| null | Expected delivery window start |
| `planned_to` | ISO 8601 \| null | Expected delivery window end |
| `pickup` | bool | Destined for a pickup point rather than a home address |
| `pickup_point` | string \| null | ServicePoint name when `pickup` is true |
| `url` | string \| null | Deep link to the parcel's tracking page |
| `raw` | dict | The full original DHL API payload |

This is the same shape that PostNL and DPD use, so the
[parcel aggregator](https://github.com/peternijssen/ha-parcel-aggregator)
and any cross-carrier dashboard can read parcels from all three
integrations the same way.

For full attribute reference, active status categories, and example
automations see [docs/sensors.md](docs/sensors.md) — or the
[examples folder](examples/) for ready-to-paste automation and
dashboard snippets.

## Parcel status reference

`status` on every parcel is one of the canonical `ParcelStatus` values
below. Use these in your automations rather than DHL's raw strings —
the raw value stays available on `raw_status` for power users.

| `status` | Meaning | DHL raw status / category that maps here |
|---|---|---|
| `registered` | DHL knows about the label but the parcel is not yet in transit | category `DATA_RECEIVED` or `LEG` |
| `in_transit` | Picked up; somewhere in DHL's network | category `UNDERWAY`, `IN_DELIVERY`, or `CUSTOMS` |
| `out_for_delivery` | On the delivery vehicle today | raw status `OUT_FOR_DELIVERY` |
| `at_pickup_point` | Arrived at the chosen ServicePoint, ready to be collected | raw status `NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT` |
| `delivered` | Handed over to the recipient, mailbox, neighbour, or picked up at a ServicePoint | category `DELIVERED` or raw status `COLLECTED_AT_PARCELSHOP` |
| `returning` | Failed delivery, on the way back to the sender | (not yet observed; will be added once the raw indicator is confirmed) |
| `problem` | Carrier reports an exception, intervention, or other issue with the parcel | category `INTERVENTION`, `EXCEPTION`, or `PROBLEM` |
| `unknown` | Raw status/category we have not mapped yet | anything else — logged once at info level so it can be added to the map |

This mapping is shared across the carriers: PostNL and DPD use the same
`ParcelStatus` values with their own raw-status mappings, so a single
event-driven automation can act on `status` regardless of carrier.

## Events

The coordinator fires events on the HA event bus when something
interesting happens to a parcel, so automations can react without
polling per-parcel sensors.

| Event | When | Payload |
|---|---|---|
| `dhl_nl_parcel_registered` | A new barcode appears in the active list | The full normalised parcel dict (`barcode`, `sender`, `status`, `raw_status`, `delivered`, `delivered_at`, `planned_from`, `planned_to`, `pickup`, `pickup_point`, `url`, `raw`) |
| `dhl_nl_parcel_status_changed` | A known barcode's normalised `status` value changes | Same payload as above plus `old_status` and `new_status` |

The coordinator suppresses events on the very first refresh after start-up
so you don't get a stampede of "registered" events for parcels that were
already in your account before HA started.

See [`examples/automations/`](examples/automations/) for ready-to-paste
event-driven automations.

## Examples

The [`examples/`](examples/) folder ships ready-to-paste snippets for
both automations and dashboards. Highlights:

- [`examples/automations/notify_when_parcel_registered.yaml`](examples/automations/notify_when_parcel_registered.yaml) — push notification when DHL announces a new parcel.
- [`examples/automations/notify_when_out_for_delivery.yaml`](examples/automations/notify_when_out_for_delivery.yaml) — alert exactly once per parcel when it's on the truck today.
- [`examples/automations/notify_when_at_servicepoint.yaml`](examples/automations/notify_when_at_servicepoint.yaml) — alert when a parcel arrives at a ServicePoint for pickup.
- [`examples/dashboards/active_parcels_grid.yaml`](examples/dashboards/active_parcels_grid.yaml) — markdown card listing every active parcel with sender, normalised status and tracking link.
- [`examples/dashboards/summary_glance.yaml`](examples/dashboards/summary_glance.yaml) — compact glance row with the day-to-day counters.
- [`examples/dashboards/next_delivery_countdown.yaml`](examples/dashboards/next_delivery_countdown.yaml) — single card showing the next expected delivery and details.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `invalid_auth` error during setup | Wrong email or password |
| `cannot_connect` error during setup | DHL API is unreachable; check your network |
| Sensors disappear after delivery | Expected — delivered shipments are filtered out |
| Sensors not updating | Check **Settings → System → Logs** for `dhl` entries |

## Related integrations

Tracking parcels from other Dutch carriers:

- [ha-postnl](https://github.com/arjenbos/ha-postnl) — PostNL parcel tracker
- [ha-dpd](https://github.com/peternijssen/ha-dpd) — DPD parcel tracker
- [ha-parcel-aggregator](https://github.com/peternijssen/ha-parcel-aggregator) — rolls up counts and next-delivery timestamps from all installed carrier integrations into a single set of sensors

## Disclaimer

This is an independent, community-built project with no affiliation, endorsement, or connection to DHL or any of its subsidiaries. The DHL eCommerce NL API is undocumented and may change without notice.

## Contributing

Pull requests and issues are welcome. Please open an issue before submitting a large change.

## License

MIT
