# DHL NL Parcel Tracker

[![Release](https://img.shields.io/github/v/release/peternijssen/ha-dhl-nl.svg)](https://github.com/peternijssen/ha-dhl-nl/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Downloads](https://img.shields.io/github/downloads/peternijssen/ha-dhl-nl/total.svg)](https://github.com/peternijssen/ha-dhl-nl/releases)

A custom Home Assistant integration that tracks your incoming and outgoing DHL eCommerce NL shipments.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Examples](#examples)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Incoming and outgoing parcel count sensors
- Per-parcel sensor per active incoming shipment
- Optional per-parcel status history timeline (opt-in; off by default)
- Next delivery datetime sensor (device class `timestamp`)
- ServicePoint sensors — en route and awaiting pickup
- Automatic lifecycle management — sensors are created and removed as parcels move through delivery
- Session recovery and re-authentication support

## Requirements

- Home Assistant 2024.7 or newer
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

Click **Configure** on the integration card. The form is split into three
sections:

### Delivered parcels

| Option | Description |
|---|---|
| Filter by | `Days` keeps delivered parcels visible for the last N days. `Number of parcels` keeps only the N most recent regardless of age. |
| Amount | The N used by the filter above. |

### Parcel history

| Option | Description |
|---|---|
| Include status history | Adds a `history` attribute to each parcel — the ordered list of status updates (timestamp, canonical status, original DHL event code), capped to the most recent 20. **Off by default.** The attribute is kept out of the recorder database. |

### Polling

| Option | Description |
|---|---|
| Refresh every | How often the integration checks DHL. Choices: **15 / 30 / 60 / 120 / 240 minutes** — default 30. A slower interval is gentler on DHL's consumer API. Changes take effect immediately, no HA restart needed. |

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
| `DHL (account) Parcel <barcode>` | Canonical status of a single incoming shipment |
| `DHL (account) Next delivery` | Earliest expected delivery datetime |
| `DHL (account) En route to ServicePoint` | Parcels in transit to a ServicePoint |
| `DHL (account) Awaiting pickup` | Parcels ready for collection at a ServicePoint |
| `DHL (account) Delivered parcels` | Recently delivered parcels (configurable window) |
| `DHL (account) Outgoing parcels` | Number of active outgoing parcels |

Every parcel exposed on a sensor attribute uses a carrier-agnostic shape:

| Key | Type | Meaning |
|---|---|---|
| `carrier` | string | `"DHL"` |
| `barcode` | string | Parcel tracking number |
| `sender` | string \| null | Sender name (e.g. webshop) |
| `receiver` | string \| null | Recipient name (e.g. the household member the parcel is addressed to) |
| `status` | `ParcelStatus` | Canonical status — see the [status reference](#parcel-status-reference) |
| `raw_status` | string \| null | Original DHL status string (for power users) |
| `delivered` | bool | Whether the parcel has been delivered |
| `delivered_at` | ISO 8601 \| null | Delivery moment, if known |
| `planned_from` | ISO 8601 \| null | Expected delivery window start |
| `planned_to` | ISO 8601 \| null | Expected delivery window end |
| `pickup` | bool | Destined for a pickup point rather than a home address |
| `pickup_point` | string \| null | ServicePoint name when `pickup` is true |
| `url` | string \| null | Deep link to the parcel's tracking page |
| `weight` | float \| null | Parcel weight in kilograms. Always `null` for DHL — the API does not expose it. |
| `dimensions` | dict \| null | Parcel dimensions in centimeters. Always `null` for DHL — same reason as `weight`. |
| `history` | list \| null | Ordered status timeline (oldest → newest), each entry `{timestamp, status, raw_status}` where `raw_status` is the DHL event code. Capped to the most recent 20. `null` unless the **Parcel history** option is enabled — see [Options](#options). |
| `raw` | dict | The original DHL API payload |

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
| `unknown` | Raw status/category we have not mapped yet | anything else — logged once at warning level with a ready-to-paste issue link so it can be added to the map |

## Events

The coordinator fires events on the HA event bus when something
interesting happens to a parcel, so automations can react without
polling per-parcel sensors.

| Event | When | Payload |
|---|---|---|
| `dhl_nl_parcel_registered` | A new barcode appears in the active list | The full parcel dict (see the table above) |
| `dhl_nl_parcel_status_changed` | A known barcode's `status` value changes | Same payload plus `old_status` and `new_status` |
| `dhl_nl_parcel_delivery_time_changed` | A known barcode's expected delivery time changes to a new value | Same payload plus `old_planned_from`, `new_planned_from`, `old_planned_to`, `new_planned_to` |

Every payload also carries a `device_id` identifying the DHL account the
parcel belongs to, so automations can tell two accounts apart.

Events do not fire for parcels that were already in your account when HA first started.

If you build automations in the UI, these same events are also available
as no-code **device triggers** (**Settings → Automations → Create → Add
trigger → Device**), scoped to the selected account's device. The raw
events above are there for templates and YAML automations.

See [`examples/automations/`](examples/automations/) for ready-to-paste
event-driven automations.

## Examples

Ready-to-paste automations and dashboard cards live in [`examples/`](examples/).

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

To capture verbose information about the DHL API responses (useful when reporting a bug or helping map a new status value), enable debug logging for the integration:

1. Add this to your `configuration.yaml`:
   ```yaml
   logger:
     default: warning
     logs:
       custom_components.dhl_nl: debug
   ```
2. Restart Home Assistant.
3. Wait for the next poll cycle (or reload the integration from **Settings → Devices & Services → DHL NL → ⋮ → Reload**).
4. Open **Settings → System → Logs**, filter for `dhl_nl`, and copy the relevant log lines (including the `DHL parcels fetched: ...` summary) into your bug report or message to the maintainer.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `invalid_auth` error during setup | Wrong email or password |
| `cannot_connect` error during setup | DHL API is unreachable; check your network |
| Sensors disappear after delivery | Expected — delivered shipments are filtered out |
| Sensors not updating | Check **Settings → System → Logs** for `dhl` entries |

## Related integrations

Tracking parcels from other Dutch carriers:

| Integration | Description |
|---|---|
| [ha-postnl](https://github.com/peternijssen/ha-postnl) | PostNL parcel tracker — maintained version. The [arjenbos/ha-postnl](https://github.com/arjenbos/ha-postnl) original is the legacy version. |
| [ha-dpd](https://github.com/peternijssen/ha-dpd) | DPD parcel tracker. |
| [ha-gls](https://github.com/peternijssen/ha-gls) | GLS Netherlands parcel tracker — no account, you enter tracking numbers yourself. |
| [ha-parcel-aggregator](https://github.com/peternijssen/ha-parcel-aggregator) | Rolls up counts and next-delivery timestamps from all installed carrier integrations into a single set of sensors. |

## Disclaimer

This is an independent, community-built project with no affiliation, endorsement, or connection to DHL or any of its subsidiaries. The DHL eCommerce NL API is undocumented and may change without notice. The maintainers have not asked DHL for permission to use this API; installing this integration may breach DHL's Terms of Service. You take any risk that follows — account suspension, service disruption, etc. No warranty (see [LICENSE](LICENSE)).

## Contributing

Pull requests and issues are welcome. Please open an issue before submitting a large change.

## License

MIT
