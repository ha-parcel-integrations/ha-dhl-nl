# Examples

Ready-to-paste Home Assistant snippets for the DHL NL integration.

| Folder | Contents |
|---|---|
| [`automations/`](automations/) | YAML automation blueprints — copy them into your `automations.yaml` or paste them into the Automation editor in **raw editor** mode. |
| [`dashboards/`](dashboards/) | Lovelace dashboard card snippets — paste them into the YAML editor of any card. |

All examples assume one DHL account; with multiple accounts you'll have
sensor IDs like `sensor.dhl_2_incoming_parcels`. Adjust the entity IDs
accordingly.

## Events used in the examples

Some examples trigger on the integration's event bus. From 2.0.0 onwards
the coordinator fires:

| Event | When | Payload (carrier-agnostic) |
|---|---|---|
| `dhl_nl_parcel_registered` | A new barcode appears in the active list | The full normalised parcel dict (`barcode`, `sender`, `status`, `delivered`, `delivered_at`, `planned_from`, `planned_to`, `pickup`, `pickup_point`, `url`, `raw_status`, `raw`) |
| `dhl_nl_parcel_status_changed` | A known barcode's normalized status changes | Same as above plus `old_status` and `new_status` (both `ParcelStatus` enum values) |

The integration suppresses events on the very first refresh after start-up
to avoid a stampede of *"registered"* events for parcels that were already
there before HA started.
