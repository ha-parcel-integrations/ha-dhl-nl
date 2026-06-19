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
| `in_transit` | Picked up; somewhere in DHL's network | category `UNDERWAY`, `IN_DELIVERY`, `CUSTOMS`, `INTERVENTION`, `EXCEPTION`, or `PROBLEM` (when no more specific raw status is set) |
| `out_for_delivery` | On the delivery vehicle today | raw status `OUT_FOR_DELIVERY` |
| `at_pickup_point` | Arrived at the chosen ServicePoint, ready to be collected | raw status `NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT` |
| `delivered` | Handed over to the recipient, mailbox, neighbour, or picked up at a ServicePoint | category `DELIVERED` or raw status `COLLECTED_AT_PARCELSHOP` |
| `returning` | Failed delivery, on the way back to the sender | (not yet observed; will be added once the raw indicator is confirmed) |
| `unknown` | Raw status/category we have not mapped yet | anything else — logged once at info level so it can be added to the map |

This mapping is shared across the carriers: PostNL and DPD use the same
`ParcelStatus` values with their own raw-status mappings, so a single
event-driven automation can act on `status` regardless of carrier.

## Example dashboard card

Shows active incoming parcels with sender and delivery window. Only visible when at least one parcel is active.

Replace `<account>` with your own entity name.

```yaml
- type: grid
  cards:
    - type: heading
      heading: Pakketten onderweg
      heading_style: title
      icon: mdi:package-variant-closed

    - type: markdown
      content: >-
        {% set ent = 'sensor.dhl_<account>_dhl_incoming_parcels' -%}
        {% set m = 'jan feb mrt apr mei jun jul aug sep okt nov dec'.split() -%}
        {% set labels = {
          'DATA_RECEIVED': 'aangemeld bij DHL',
          'UNDERWAY': 'onderweg'
        } -%}
        {% macro hm(d) -%}{{ d.strftime('%-H') }}{{ ':' ~ d.strftime('%M') if d.minute }}{%- endmacro -%}
        {% for p in state_attr(ent, 'parcels') or [] -%}
        {% set ti = p.receivingTimeIndication -%}
        {% set cat = p.category | default('', true) -%}
        {% set label = labels[cat] if cat in labels else cat | lower | replace('_', ' ') -%}
        {% set sender = p.sender.name if p.sender and p.sender.name else 'onbekende afzender' -%}
        - 📦 DHL van **{{ sender }}**{% if ti and ti.start and ti.end %}{% set s =
        as_datetime(ti.start) %}{% set e = as_datetime(ti.end) %} · {{ s.day }} {{
        m[s.month - 1] }}, {{ hm(s) }}–{{ hm(e) }}u{% else %} · {{ label }}{% endif %}
        {% endfor -%}

  visibility:
    - condition: numeric_state
      above: 0
      entity: sensor.dhl_<account>_dhl_incoming_parcels
```

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
