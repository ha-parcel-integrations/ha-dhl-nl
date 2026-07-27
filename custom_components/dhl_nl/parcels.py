"""Pure parcel mapping, normalization and list helpers for DHL.

No I/O and no Home Assistant objects beyond the config entry's options: this is
the carrier-specific status mapping and canonical-shape logic, kept apart from
the coordinator (fetching, caching, events) so it stays trivially unit-testable.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry

from .const import (
    ACTIVE_CATEGORIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    STATUS_AT_SERVICE_POINT,
    STATUS_COLLECTED_AT_SERVICE_POINT,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)


# Granular DHL status strings → canonical ParcelStatus. Status takes
# precedence over category because it is more specific: the category only
# distinguishes registered / in-transit / delivered / problem, so the finer
# states (out_for_delivery, at_pickup_point, returning) MUST be mapped here or
# they fall through to a coarser — and for returns, wrong — category.
#
# Sourced from DHL's official `parcel-nl` status catalogue (the same vocabulary
# this consumer API reports). Grouped by bucket; each entry is backed by the
# catalogue's description. Extend from that catalogue as new statuses appear.
_STATUS_MAP: dict[str, ParcelStatus] = {
    # --- Out for delivery (with the courier) ---
    "OUT_FOR_DELIVERY": ParcelStatus.OUT_FOR_DELIVERY,
    "LOAD_VEHICLE": ParcelStatus.OUT_FOR_DELIVERY,
    "PARCEL_INTO_FALLBACK": ParcelStatus.OUT_FOR_DELIVERY,
    "PARCEL_WILL_BE_DELIVERED_SOON": ParcelStatus.OUT_FOR_DELIVERY,
    # --- Ready for the recipient to collect (ServicePoint / locker / depot) ---
    STATUS_AT_SERVICE_POINT: ParcelStatus.AT_PICKUP_POINT,
    "AWAITING_RECEIVER_COLLECTION": ParcelStatus.AT_PICKUP_POINT,
    "CLOSED_AWAITING_COLLECTION": ParcelStatus.AT_PICKUP_POINT,
    "DELIVERED_AT_ACCESSPOINT": ParcelStatus.AT_PICKUP_POINT,
    "DELIVERED_AT_PARCELSTATION": ParcelStatus.AT_PICKUP_POINT,
    "DELIVERY_CODE_MISSING_PICK_UP": ParcelStatus.AT_PICKUP_POINT,
    "ON_HOLD_FOR_COLLECTION": ParcelStatus.AT_PICKUP_POINT,
    "PARCEL_FOUND_AT_PARCELSHOP": ParcelStatus.AT_PICKUP_POINT,
    "PARCEL_HELD_FOR_COLLECTION_AT_LOCAL_DEPOT": ParcelStatus.AT_PICKUP_POINT,
    "REMINDER_FOR_COLLECTION_SENT_EMAIL": ParcelStatus.AT_PICKUP_POINT,
    "REMINDER_FOR_COLLECTION_SENT_LETTER": ParcelStatus.AT_PICKUP_POINT,
    "REMINDER_FOR_COLLECTION_SENT_SMS": ParcelStatus.AT_PICKUP_POINT,
    # --- Collected by / delivered to the recipient (terminal) ---
    STATUS_COLLECTED_AT_SERVICE_POINT: ParcelStatus.DELIVERED,
    "COLLECTED_AT_ACCESSPOINT": ParcelStatus.DELIVERED,
    "COLLECTED_AT_PARCELSTATION": ParcelStatus.DELIVERED,
    "SHIPMENT_COLLECTED": ParcelStatus.DELIVERED,
    # Direct delivery variants — the parcel reached the recipient (or their
    # neighbour / safe place / mailbox). All terminal; without these they fall
    # through to a coarser "in delivery" category and never reach DELIVERED.
    "DELIVERED_AT_NEIGHBOURS": ParcelStatus.DELIVERED,
    "DELIVERED_AT_PREFERED_NEIGHBOURS": ParcelStatus.DELIVERED,
    "DELIVERED_AT_SAFEPLACE": ParcelStatus.DELIVERED,
    "DELIVERED_IN_MAILBOX": ParcelStatus.DELIVERED,
    "DELIVERED_DAMAGED": ParcelStatus.DELIVERED,
    "DELIVERED_NOT_IN_TIME": ParcelStatus.DELIVERED,
    "DELIVERED_NO_CODE_VALIDATION": ParcelStatus.DELIVERED,
    # --- Failed delivery, going back to the sender ---
    # None of these are expressible via the category map, which would mislabel
    # them IN_TRANSIT (UNDERWAY) or PROBLEM (INTERVENTION/EXCEPTION).
    "ADDRESS_UNKNOWN": ParcelStatus.RETURNING,
    "CUSTOMS_DATA_INCORRECT_RETURN_TO_SHIPPER": ParcelStatus.RETURNING,
    "DAMAGE_RETURN": ParcelStatus.RETURNING,
    "DELIVERED_AT_SHIPPER": ParcelStatus.RETURNING,
    "DELIVERY_CODE_MISSING_RETURN": ParcelStatus.RETURNING,
    "DELIVERY_DATA_INCORRECT_RETURN": ParcelStatus.RETURNING,
    "EXPECTED_RETURN_DELIVERED_AT_SHIPPER_CALCULATED": ParcelStatus.RETURNING,
    "INTERVENTION_RECEIVER_REQUEST_DELIVERY_CANCELLED": ParcelStatus.RETURNING,
    "INTERVENTION_REQUEST_CANCEL_INTERVENTION": ParcelStatus.RETURNING,
    "INTERVENTION_REQUEST_CANCEL_INTERVENTION_SUSPECTED_FRAUD": ParcelStatus.RETURNING,
    "INTERVENTION_SHIPPER_REQUEST_DELIVERY_CANCELLED": ParcelStatus.RETURNING,
    "INVALID_SHIPMENT_SPECIFICATION_RETURN": ParcelStatus.RETURNING,
    "MISROUTED_RETURN_TO_SHIPPER": ParcelStatus.RETURNING,
    "NOT_HOME_RETURN_TO_SHIPPER": ParcelStatus.RETURNING,
    "NO_MONEY_RETURN": ParcelStatus.RETURNING,
    "ON_ROUTE_TO_SHIPPER": ParcelStatus.RETURNING,
    "PARCELSTATION_DELIVERY_UNSUCCESFULL_RETURN": ParcelStatus.RETURNING,
    "PARCEL_ALREADY_RETURNED": ParcelStatus.RETURNING,
    "PARCEL_READY_FOR_RETURN_TO_HUB": ParcelStatus.RETURNING,
    "PARCEL_RELABELED_FOR_RETURN_TO_SHIPPER": ParcelStatus.RETURNING,
    "PARCEL_RETURNED_FROM_ROUTE": ParcelStatus.RETURNING,
    "PARCEL_SCANNED_AT_RETURN_HUB": ParcelStatus.RETURNING,
    "PARCEL_SCANNED_FOR_RETURN_TO_HUB": ParcelStatus.RETURNING,
    "PARCEL_TOO_HEAVY_RETURN": ParcelStatus.RETURNING,
    "PARCEL_TOO_LARGE_RETURN": ParcelStatus.RETURNING,
    "POSTAL_CODE_INCORRECT": ParcelStatus.RETURNING,
    "POSTPROCESS_DELIVERED_AT_SHIPPER": ParcelStatus.RETURNING,
    "POSTPROCESS_RETURN_CONSOLIDATION": ParcelStatus.RETURNING,
    "POSTPROCESS_RETURN_CONSOLIDATION_DEPART": ParcelStatus.RETURNING,
    "POSTPROCESS_RETURN_CONSOLIDATION_LOAD": ParcelStatus.RETURNING,
    "PO_BOX": ParcelStatus.RETURNING,
    "RECEIVER_RETURN": ParcelStatus.RETURNING,
    "RECEIVER_UNKNOWN_RETURN": ParcelStatus.RETURNING,
    "REFUSED_AT_PARCELSHOP": ParcelStatus.RETURNING,
    "REFUSED_BY_RECEIVER": ParcelStatus.RETURNING,
    "REFUSED_NOT_COLLECTED": ParcelStatus.RETURNING,
    "REFUSED_RETURN": ParcelStatus.RETURNING,
    "REFUSED_RETURN_TO_DD": ParcelStatus.RETURNING,
    "RETURNED_NOT_COLLECTED": ParcelStatus.RETURNING,
    "RETURNED_TO_SHIPPER": ParcelStatus.RETURNING,
    "RETURN_DELIVERED_AT_SHIPPER_CALCULATED": ParcelStatus.RETURNING,
    "SPONTANEOUS_RETURN": ParcelStatus.RETURNING,
    "STORAGE_PERIOD_ENDED_AT_ACCESSPOINT": ParcelStatus.RETURNING,
    "STORAGE_PERIOD_ENDED_AT_PARCELSHOP": ParcelStatus.RETURNING,
    "STORAGE_PERIOD_ENDED_AT_PARCELSTATION": ParcelStatus.RETURNING,
    "UNJUSTIFIED_SPONTANEOUS_RETURN": ParcelStatus.RETURNING,
    # --- Still on its way (mapped explicitly to avoid a wrong category) ---
    # Receiver asked for delivery at another time/date/place — benign, the
    # parcel is still on its way. Without these they fall through to the
    # INTERVENTION category and are mislabelled as PROBLEM.
    "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_ANOTHER_TIME/DATE": ParcelStatus.IN_TRANSIT,
    "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_ACCESSPOINT": ParcelStatus.IN_TRANSIT,
    "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_NEIGHBOURS": ParcelStatus.IN_TRANSIT,
    "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_PARCELSHOP": ParcelStatus.IN_TRANSIT,
    "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_PARCELSTATION": ParcelStatus.IN_TRANSIT,
    "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_PREFERRED_NEIGHBOURS": ParcelStatus.IN_TRANSIT,
}

# DHL category (high-level state) → canonical ParcelStatus. Used as a
# fallback when no specific status mapping applies. ``DELIVERED`` here
# is the only terminal category; everything else is some flavour of
# "in motion".
_CATEGORY_MAP: dict[str, ParcelStatus] = {
    "DATA_RECEIVED": ParcelStatus.REGISTERED,
    "LEG": ParcelStatus.REGISTERED,
    "CUSTOMS": ParcelStatus.IN_TRANSIT,
    "UNDERWAY": ParcelStatus.IN_TRANSIT,
    "IN_DELIVERY": ParcelStatus.IN_TRANSIT,
    "INTERVENTION": ParcelStatus.PROBLEM,
    "EXCEPTION": ParcelStatus.PROBLEM,
    "PROBLEM": ParcelStatus.PROBLEM,
    "DELIVERED": ParcelStatus.DELIVERED,
}

# New-issue link surfaced in the unknown-status warnings so users can paste a
# ready-made line into a bug report.
# Points at the pre-filled issue template rather than a blank form, so a
# user following this link from their log lands somewhere that already
# asks the right questions.
_NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-dhl-nl/issues/new"
    "?template=unrecognised_status.yml"
)

# Already-logged values so we surface each unmapped one only once per HA
# session. Parcel-level keys on (status, category); history keys on
# (event key, phase).
_unmapped_statuses_logged: set[tuple[str, str]] = set()
_unmapped_event_keys_logged: set[tuple[str, str]] = set()

def map_parcel_status(parcel: dict) -> ParcelStatus:
    """Map a raw DHL parcel to a canonical :class:`ParcelStatus`.

    Strategy: prefer the granular ``status`` field for known terminal /
    pickup-point situations, fall back to the high-level ``category``,
    and surface unknown raw values via a one-shot info-level log so we
    can extend the maps as new statuses appear.
    """
    raw_status = parcel.get("status") or ""
    raw_category = parcel.get("category") or ""

    if raw_status in _STATUS_MAP:
        return _STATUS_MAP[raw_status]
    if raw_category in _CATEGORY_MAP:
        return _CATEGORY_MAP[raw_category]

    key = (raw_status, raw_category)
    if key not in _unmapped_statuses_logged:
        _unmapped_statuses_logged.add(key)
        _LOGGER.warning(
            "Unrecognised DHL status — help us map it. Open an issue and "
            "paste this line: %s\n  [parcel] status=%s category=%s "
            "→ reported as 'unknown'",
            _NEW_ISSUE_URL,
            raw_status,
            raw_category,
        )
    return ParcelStatus.UNKNOWN


def map_event_status(
    event_key: str | None, phase: str | None
) -> ParcelStatus | None:
    """Map a track-trace event to a canonical status, reusing the parcel maps.

    DHL's per-event ``key`` shares the granular ``status`` vocabulary and the
    ``phase`` shares the ``category`` vocabulary, so the same two maps drive
    history: the granular ``_STATUS_MAP`` first (more specific), then the
    coarser ``_CATEGORY_MAP`` on the phase. Unmapped → ``None`` (history keeps
    ``status: null``) plus a one-shot warning with copy-paste issue text.
    """
    if event_key and event_key in _STATUS_MAP:
        return _STATUS_MAP[event_key]
    if phase and phase in _CATEGORY_MAP:
        return _CATEGORY_MAP[phase]

    key = (event_key or "", phase or "")
    if key not in _unmapped_event_keys_logged:
        _unmapped_event_keys_logged.add(key)
        _LOGGER.warning(
            "Unrecognised DHL status — help us map it. Open an issue and "
            "paste this line: %s\n  [history] key=%s phase=%s "
            "→ reported as 'unknown'",
            _NEW_ISSUE_URL,
            event_key,
            phase,
        )
    return None


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Track-trace timestamps are UTC (``Z`` suffix); a naive value is treated
    as UTC so a list always sorts without crashing on a mixed set.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _extract_events(track_trace: list | dict | None) -> list[tuple[dict, str | None]]:
    """Flatten a track-trace response into ``(event, phase)`` pairs.

    The response is a JSON array (one object per matched parcel — in practice
    a single object). Events live under ``[0].view.phases[].events[]``; each
    is tagged with its parent phase so the per-event mapping can fall back to
    the phase.
    """
    if not track_trace:
        return []
    first = track_trace[0] if isinstance(track_trace, list) else track_trace
    view = (first or {}).get("view") or {}
    pairs: list[tuple[dict, str | None]] = []
    for phase_block in view.get("phases") or []:
        phase = phase_block.get("phase")
        for event in phase_block.get("events") or []:
            pairs.append((event, phase))
    return pairs


def build_history(
    track_trace: list | dict | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from a track-trace response.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers. DHL has no human event text, so ``raw_status`` is the
    event ``key`` (a code), mirroring how the parcel-level ``raw_status`` is
    the carrier's own status string. Sorted oldest → newest by timestamp
    (DHL returns phases newest-first) and capped to the most recent
    ``max_events``.
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event, phase in _extract_events(track_trace):
        timestamp = event.get("timestamp")
        if not timestamp:
            continue
        event_key = event.get("key")
        entry = {
            "timestamp": timestamp,
            "status": map_event_status(event_key, phase),
            "raw_status": event_key,
        }
        dt = _parse_iso(timestamp)
        if dt is None:
            unparseable.append(entry)
        else:
            parseable.append((dt, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def _delivery_window(parcel: dict) -> tuple[str | None, str | None]:
    """Return (from, to) ISO 8601 strings from receivingTimeIndication."""
    indication = parcel.get("receivingTimeIndication") or {}
    indication_type = indication.get("indicationType")
    if indication_type == "MomentIndication":
        return indication.get("moment"), None
    if indication_type == "IntervalIndication":
        return indication.get("start"), indication.get("end")
    return None, None


def _tracking_url(parcel: dict) -> str | None:
    """Construct the my.dhlecommerce.nl tracking URL for a parcel.

    The portal keys the lookup on the **receiver's** postcode, not the delivery
    destination's. For a normal home delivery the two match, but a parcel routed
    to a ServicePoint has the pickup point's postcode as ``destination`` — and
    the portal returns "package not found" for that, so the receiver postcode is
    the correct one (it is also what the track-trace API call keys on, see
    ``async_get_track_trace``). Fall back to ``destination`` only when the
    receiver has no postcode.

    Returns ``None`` when the parcel is missing the barcode or both postcodes.
    The URL pattern is taken from the public portal and depends on DHL keeping
    it stable.
    """
    barcode = parcel.get("barcode")
    postal = (
        ((parcel.get("receiver") or {}).get("address") or {}).get("postalCode")
        or ((parcel.get("destination") or {}).get("address") or {}).get("postalCode")
        or ""
    )
    postal = postal.replace(" ", "")
    if not barcode or not postal:
        return None
    return f"https://my.dhlecommerce.nl/portal/tracktrace/{barcode}/{postal}"


def normalize_parcel(parcel: dict, *, history: list[dict] | None = None) -> dict:
    """Return a carrier-agnostic parcel dict with the original DHL payload under ``raw``.

    ``weight`` and ``dimensions`` are part of the canonical shape every carrier
    integration publishes but DHL does not expose them in any endpoint we know
    of, so they are always ``None`` here. Aggregator and cross-carrier cards
    can still rely on the keys being present.

    ``history`` is the optional per-parcel status timeline (opt-in option,
    default off → ``None``). It comes from a separate track-trace call and
    stays top-level so it survives the aggregator's ``strip_raw()``.
    """
    sender = parcel.get("sender") or {}
    receiver = parcel.get("receiver") or {}
    destination = parcel.get("destination") or {}
    delivered = parcel.get("category") == "DELIVERED"
    moment_from, moment_to = _delivery_window(parcel)
    is_pickup = destination.get("locationType") == "SERVICEPOINT"

    return {
        "carrier": "DHL",
        "barcode": parcel.get("barcode"),
        "sender": sender.get("name"),
        "receiver": receiver.get("name"),
        "status": map_parcel_status(parcel),
        "raw_status": parcel.get("status"),
        "delivered": delivered,
        "delivered_at": moment_from if delivered else None,
        "planned_from": None if delivered else moment_from,
        "planned_to": None if delivered else moment_to,
        "pickup": is_pickup,
        "pickup_point": destination.get("name") if is_pickup else None,
        "url": _tracking_url(parcel),
        "weight": None,
        "dimensions": None,
        "history": history,
        "raw": parcel,
    }


def filter_active_parcels(parcels: list[dict]) -> list[dict]:
    """Return only active incoming parcels (not returns, in an active category)."""
    return [
        p for p in parcels
        if not p.get("isReturn", True)
        and p.get("category") in ACTIVE_CATEGORIES
    ]


def filter_delivered_parcels(parcels: list[dict]) -> list[dict]:
    """Return delivered incoming parcels (not returns, category DELIVERED)."""
    return [
        p for p in parcels
        if not p.get("isReturn", True)
        and p.get("category") == "DELIVERED"
    ]


def filter_active_sent_shipments(shipments: list[dict]) -> list[dict]:
    """Return only outgoing shipments that are still in transit (not yet delivered)."""
    return [
        s for s in shipments
        if s.get("type") == "outgoing"
        and s.get("category") in ACTIVE_CATEGORIES
    ]


def filter_delivered_sent_shipments(shipments: list[dict]) -> list[dict]:
    """Return outgoing shipments that have been delivered."""
    return [
        s for s in shipments
        if s.get("type") == "outgoing"
        and s.get("category") == "DELIVERED"
    ]


def filter_active_returns(parcels: list[dict]) -> list[dict]:
    """Return active return parcels (on their way back to the shipper).

    Sourced from the same receiver-parcel-api list as incoming parcels — a
    webshop-generated return label never appears on the sent-shipments
    endpoint because the account holder isn't its sender of record. This
    is why returns are folded into the "outgoing" sensors alongside
    ``DhlSentShipmentsCoordinator``'s own data rather than exposed under a
    DHL-specific "return" name — externally a return is just one more way
    a parcel becomes outgoing, same as PostNL's model.
    """
    return [
        p for p in parcels
        if p.get("isReturn")
        and p.get("category") in ACTIVE_CATEGORIES
    ]


def filter_delivered_returns(parcels: list[dict]) -> list[dict]:
    """Return return parcels that have arrived back at the shipper."""
    return [
        p for p in parcels
        if p.get("isReturn")
        and p.get("category") == "DELIVERED"
    ]


def _delivery_dt(parcel: dict) -> datetime | None:
    """Parse the delivery datetime from a parcel's receivingTimeIndication."""
    indication = parcel.get("receivingTimeIndication") or {}
    indication_type = indication.get("indicationType")
    if indication_type == "MomentIndication":
        moment_str = indication.get("moment")
    elif indication_type == "IntervalIndication":
        moment_str = indication.get("start")
    else:
        return None
    if not moment_str:
        return None
    try:
        dt = datetime.fromisoformat(moment_str.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Apply the configured delivered-filter option to a list of raw parcels.

    Shared by both coordinators — the same days/count option governs
    delivered incoming parcels, delivered returns, and delivered sent
    shipments.
    """
    options = entry.options
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    filter_amount = int(options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT))

    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=filter_amount)
        return [
            p for p in parcels
            if (dt := _delivery_dt(p)) is None or dt >= cutoff
        ]

    # "parcels" — return the most recent N
    return parcels[:filter_amount]


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalized parcels sorted by the ISO timestamp at ``key_field``.

    Parcels whose value is missing or unparseable always sort to the end,
    regardless of ``descending`` — so freshly registered parcels without
    an ETA stay visible at the bottom instead of jumping to the top.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        value = parcel.get(key_field)
        if not isinstance(value, str) or not value:
            without_ts.append(parcel)
            continue
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            without_ts.append(parcel)
            continue
        with_ts.append((dt, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [p for _, p in with_ts] + without_ts
