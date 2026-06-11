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

| Entity | Description |
|--------|-------------|
| `sensor.<account>_dhl_incoming_parcels` | Number of active incoming parcels |
| `sensor.<account>_dhl_parcel_<barcode>` | Status of a single incoming shipment |
| `sensor.<account>_dhl_next_delivery` | Earliest expected delivery datetime |
| `sensor.<account>_dhl_en_route_to_service_point` | Parcels in transit to a ServicePoint |
| `sensor.<account>_dhl_parcels_awaiting_pickup` | Parcels ready for collection at a ServicePoint |
| `sensor.<account>_dhl_delivered_parcels` | Recently delivered parcels (configurable window) |
| `sensor.<account>_dhl_outgoing_parcels` | Number of active outgoing shipments |

For full attribute reference, active status categories, and example automations see [docs/sensors.md](docs/sensors.md).

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

## Disclaimer

This is an independent, community-built project with no affiliation, endorsement, or connection to DHL or any of its subsidiaries. The DHL eCommerce NL API is undocumented and may change without notice.

## Contributing

Pull requests and issues are welcome. Please open an issue before submitting a large change.

## License

MIT
