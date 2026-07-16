"""Coordinator for the DHL Package Tracker integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DhlApiClient, DhlApiError, DhlAuthError
from .const import (
    ACTIVE_CATEGORIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_REFRESH_INTERVAL,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_INCLUDE_HISTORY,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    HISTORY_MAX_EVENTS,
    STATUS_AT_SERVICE_POINT,
    STATUS_COLLECTED_AT_SERVICE_POINT,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Granular DHL status strings → canonical ParcelStatus. Status takes
# precedence over category because it is more specific.
_STATUS_MAP: dict[str, ParcelStatus] = {
    STATUS_AT_SERVICE_POINT: ParcelStatus.AT_PICKUP_POINT,
    STATUS_COLLECTED_AT_SERVICE_POINT: ParcelStatus.DELIVERED,
    "OUT_FOR_DELIVERY": ParcelStatus.OUT_FOR_DELIVERY,
    # Receiver asked for delivery at another time/date — benign, the parcel
    # is still on its way. Mapped explicitly so it does not fall through to
    # the INTERVENTION category, which would mislabel it as PROBLEM.
    "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_ANOTHER_TIME/DATE": ParcelStatus.IN_TRANSIT,
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
_NEW_ISSUE_URL = "https://github.com/ha-parcel-integrations/ha-dhl-nl/issues/new"

# Already-logged values so we surface each unmapped one only once per HA
# session. Parcel-level keys on (status, category); history keys on
# (event key, phase).
_unmapped_statuses_logged: set[tuple[str, str]] = set()
_unmapped_event_keys_logged: set[tuple[str, str]] = set()


def _refresh_interval(entry: ConfigEntry) -> timedelta:
    """Return the configured refresh interval as a ``timedelta``."""
    minutes = int(entry.options.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL))
    return timedelta(minutes=minutes)


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

    Returns ``None`` when the parcel is missing the barcode or destination
    postcode. The URL pattern is taken from the public portal and depends on
    DHL keeping it stable.
    """
    barcode = parcel.get("barcode")
    postal = (((parcel.get("destination") or {}).get("address") or {}).get("postalCode") or "")
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


class DhlCoordinator(DataUpdateCoordinator[list[dict]]):
    """Coordinator that polls the DHL parcels API on a fixed schedule."""

    def __init__(self, hass: HomeAssistant, client: DhlApiClient, entry: ConfigEntry) -> None:
        """Initialise the coordinator.

        Args:
            hass: The Home Assistant instance.
            client: An authenticated :class:`DhlApiClient` instance.
            entry: The config entry, used to read options for the delivered
                filter and the configured refresh interval.
        """
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=_refresh_interval(entry),
        )
        self._client = client
        self.delivered: list[dict] = []
        # Return parcels — sourced from the same parcels list as incoming,
        # filtered on isReturn instead of excluded. ``returning`` mirrors
        # ``data`` (active, sorted by planned_from); ``delivered_outgoing``
        # mirrors ``delivered`` (completed, sorted by delivered_at desc).
        self.returning: list[dict] = []
        self.delivered_outgoing: list[dict] = []
        # barcode -> last seen ParcelStatus. ``None`` on the first refresh so
        # we can suppress events for parcels that already existed when the
        # integration started (we do not know their previous state).
        self._known_state: dict[str, ParcelStatus] | None = None
        # barcode -> last seen (planned_from, planned_to) tuple. Mirrors
        # ``_known_state`` for delivery-time-change detection.
        self._known_delivery_times: (
            dict[str, tuple[str | None, str | None]] | None
        ) = None
        # barcode -> last seen ParcelStatus for *outgoing* parcels (returns +
        # sent), tracked across the active (``returning``) and delivered
        # (``delivered_outgoing``) buckets so a status change or a
        # transition-to-delivered fires an outgoing event. ``None`` on the
        # first refresh for the same suppression reason as ``_known_state``.
        self._known_outgoing_state: dict[str, ParcelStatus] | None = None
        # barcode -> {"history": [...], "_raw_status": str}. The track-trace
        # call is an extra HTTP request per parcel, so we only make it when
        # the history option is on, and only refetch when a parcel's raw
        # status changes (history only grows on a status change). The cache
        # lives for the integration's lifetime (resets on HA restart).
        self._history_cache: dict[str, dict] = {}
        # Cached device id for this account, attached to every fired event so
        # device-trigger automations can filter to a specific DHL account.
        # ``None`` until the device exists (i.e. the sensors are set up).
        self._cached_device_id: str | None = None
        # Timestamp of the last successful poll, surfaced by a diagnostic
        # sensor so users can alert on a silently-stale integration (the
        # count sensors only change when a value changes, not every poll).
        self.last_success_time: datetime | None = None

    def _device_id(self) -> str | None:
        """Resolve (and cache) this account's device id for event payloads.

        Looked up from the device registry by config entry. Stays ``None``
        until the device has been registered (the sensors create it on first
        setup), which is harmless because events are suppressed on the very
        first refresh anyway.
        """
        if self._cached_device_id is not None:
            return self._cached_device_id
        registry = dr.async_get(self.hass)
        device = next(
            iter(dr.async_entries_for_config_entry(registry, self.config_entry.entry_id)),
            None,
        )
        if device is not None:
            self._cached_device_id = device.id
        return self._cached_device_id

    @property
    def _include_history(self) -> bool:
        """Whether the opt-in per-parcel history option is enabled."""
        return bool(
            self.config_entry.options.get(
                CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
            )
        )

    def _normalize(self, parcel: dict) -> dict:
        """Normalize a raw parcel, attaching any cached history timeline."""
        barcode = parcel.get("barcode") or ""
        history = (self._history_cache.get(barcode) or {}).get("history")
        return normalize_parcel(parcel, history=history)

    async def _async_update_data(self) -> list[dict]:
        try:
            raw = await self._client.async_get_parcels()
        except DhlAuthError as err:
            raise ConfigEntryAuthFailed("DHL authentication failed") from err
        except DhlApiError as err:
            raise UpdateFailed(f"DHL error: {err}") from err
        # aiohttp.ClientError is wrapped automatically by DataUpdateCoordinator.

        active = filter_active_parcels(raw)
        delivered = self._apply_delivered_filter(filter_delivered_parcels(raw))
        returning = filter_active_returns(raw)
        delivered_returns = self._apply_delivered_filter(filter_delivered_returns(raw))
        _LOGGER.debug(
            "DHL parcels fetched: %d total, %d active, %d delivered, "
            "%d returning, %d returned",
            len(raw), len(active), len(delivered), len(returning), len(delivered_returns),
        )
        # Fetch the track-trace timeline for active + delivered parcels when
        # the option is on, before normalizing so the history is attached.
        # Returns are excluded — track-trace is a receiver-role endpoint.
        await self._enrich_history(active + delivered)

        self.delivered = sort_parcels_by_ts(
            [self._normalize(p) for p in delivered],
            "delivered_at",
            descending=True,
        )
        normalized_active = sort_parcels_by_ts(
            [self._normalize(p) for p in active], "planned_from"
        )
        self.returning = sort_parcels_by_ts(
            [self._normalize(p) for p in returning], "planned_from"
        )
        self.delivered_outgoing = sort_parcels_by_ts(
            [self._normalize(p) for p in delivered_returns],
            "delivered_at",
            descending=True,
        )

        self._fire_change_events(normalized_active)

        # Outgoing = returns (active) + delivered returns, combined so a
        # transition from in-transit to delivered is visible in one set.
        outgoing = self.returning + self.delivered_outgoing
        self._fire_outgoing_change_events(outgoing)

        self._known_state = {
            p["barcode"]: p["status"]
            for p in normalized_active
            if p.get("barcode")
        }
        self._known_delivery_times = {
            p["barcode"]: (p.get("planned_from"), p.get("planned_to"))
            for p in normalized_active
            if p.get("barcode")
        }
        self._known_outgoing_state = {
            p["barcode"]: p["status"]
            for p in outgoing
            if p.get("barcode")
        }

        self.last_success_time = datetime.now(timezone.utc)
        return normalized_active

    def _fire_change_events(self, parcels: list[dict]) -> None:
        """Fire events for newly-registered parcels and parcel transitions.

        Silent on the very first refresh — we cannot reliably know which
        parcels are "new" to the user vs. "already there before HA started".
        From the second refresh onward, every parcel that was not present
        before yields one ``dhl_nl_parcel_registered`` event, every parcel
        whose normalized status changed yields one
        ``dhl_nl_parcel_status_changed`` event, and every parcel whose
        ``planned_from`` or ``planned_to`` changed to a non-null value
        yields one ``dhl_nl_parcel_delivery_time_changed`` event.
        """
        if self._known_state is None:
            return

        known_times = self._known_delivery_times or {}
        device_id = self._device_id()

        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode:
                continue
            new_status = parcel["status"]
            if barcode not in self._known_state:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_parcel_registered",
                    {**parcel, "device_id": device_id},
                )
                continue

            if self._known_state[barcode] != new_status:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_parcel_status_changed",
                    {
                        **parcel,
                        "device_id": device_id,
                        "old_status": self._known_state[barcode],
                        "new_status": new_status,
                    },
                )

            old_from, old_to = known_times.get(barcode, (None, None))
            new_from = parcel.get("planned_from")
            new_to = parcel.get("planned_to")
            # Fire only when at least one of the two ends up with a real
            # (non-null) value AND that value differs from the last-known
            # one. value -> null transitions are intentionally silent —
            # they mean the carrier dropped the ETA, which is not what
            # users want to be paged about.
            from_changed = new_from is not None and new_from != old_from
            to_changed = new_to is not None and new_to != old_to
            if from_changed or to_changed:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_parcel_delivery_time_changed",
                    {
                        **parcel,
                        "device_id": device_id,
                        "old_planned_from": old_from,
                        "new_planned_from": new_from,
                        "old_planned_to": old_to,
                        "new_planned_to": new_to,
                    },
                )

    def _fire_outgoing_change_events(self, parcels: list[dict]) -> None:
        """Fire status/delivered events for outgoing parcels (returns + sent).

        Silent on the very first refresh (``_known_outgoing_state is None``),
        matching ``_fire_change_events``. From the second refresh onward, every
        outgoing parcel whose normalized status transitions **to**
        ``DELIVERED`` yields one ``dhl_nl_outgoing_parcel_delivered`` event, and
        every other status change yields one
        ``dhl_nl_outgoing_parcel_status_changed`` event. ``delivered`` takes
        precedence over ``status_changed`` for that final transition, so the
        terminal hop fires exactly one (dedicated) event, not both. A parcel
        that is already delivered the first time it is seen never fires (its
        status did not change). There is no outgoing ``registered`` or
        ``delivery_time_changed`` event — those are intentionally out of scope.
        """
        if self._known_outgoing_state is None:
            return

        device_id = self._device_id()

        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode or barcode not in self._known_outgoing_state:
                continue
            old_status = self._known_outgoing_state[barcode]
            new_status = parcel["status"]
            if new_status == old_status:
                continue

            if new_status == ParcelStatus.DELIVERED:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_outgoing_parcel_delivered",
                    {**parcel, "device_id": device_id},
                )
            else:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_outgoing_parcel_status_changed",
                    {
                        **parcel,
                        "device_id": device_id,
                        "old_status": old_status,
                        "new_status": new_status,
                    },
                )

    async def _enrich_history(self, parcels: list[dict]) -> None:
        """Populate ``self._history_cache`` from the track-trace endpoint.

        Opt-in only. For each parcel we call track-trace on first sight and
        again only when its raw ``status`` changes (history grows on status
        changes), mirroring DPD's detail-cache cost control. Best-effort: a
        ``None`` response leaves any prior history in place and never breaks
        the poll. The query needs the parcel's ``parcelId`` (uuid) and the
        receiver's postcode, both from the list endpoint.
        """
        if not self._include_history:
            return
        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode:
                continue
            raw_status = parcel.get("status") or ""
            cached = self._history_cache.get(barcode)
            if cached is not None and cached.get("_raw_status") == raw_status:
                continue
            postal = (
                ((parcel.get("receiver") or {}).get("address") or {}).get(
                    "postalCode"
                )
                or ""
            ).replace(" ", "")
            parcel_id = parcel.get("parcelId")
            if not postal or not parcel_id:
                continue
            track_trace = await self._client.async_get_track_trace(
                barcode, postal, parcel_id
            )
            if track_trace is None:
                continue
            self._history_cache[barcode] = {
                "history": build_history(track_trace),
                "_raw_status": raw_status,
            }

    def _apply_delivered_filter(self, parcels: list[dict]) -> list[dict]:
        """Apply the configured filter to the delivered parcels list."""
        return _apply_delivered_filter(parcels, self.config_entry)


class DhlSentShipmentsCoordinator(DataUpdateCoordinator[list[dict]]):
    """Coordinator that polls the DHL sent shipments API on a fixed schedule."""

    def __init__(self, hass: HomeAssistant, client: DhlApiClient, entry: ConfigEntry) -> None:
        """Initialise the coordinator.

        Args:
            hass: The Home Assistant instance.
            client: An authenticated :class:`DhlApiClient` instance.
            entry: The config entry, used to read the configured refresh interval.
        """
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_sent",
            update_interval=_refresh_interval(entry),
        )
        self._client = client
        # Delivered own-sender shipments. In practice this stays empty for
        # almost every account (see filter_active_returns docstring), but is
        # tracked for parity so the sensor layer can merge it with delivered
        # returns under a single "delivered outgoing" sensor without special
        # casing which data source actually has content.
        self.delivered: list[dict] = []

    async def _async_update_data(self) -> list[dict]:
        try:
            raw = await self._client.async_get_sent_shipments()
        except DhlAuthError as err:
            raise ConfigEntryAuthFailed("DHL authentication failed") from err
        except DhlApiError as err:
            raise UpdateFailed(f"DHL error (sent): {err}") from err
        # aiohttp.ClientError is wrapped automatically by DataUpdateCoordinator.

        active = filter_active_sent_shipments(raw)
        delivered = _apply_delivered_filter(
            filter_delivered_sent_shipments(raw), self.config_entry
        )
        _LOGGER.debug(
            "DHL sent shipments fetched: %d total, %d active, %d delivered",
            len(raw), len(active), len(delivered),
        )
        self.delivered = sort_parcels_by_ts(
            [normalize_parcel(s) for s in delivered],
            "delivered_at",
            descending=True,
        )
        return sort_parcels_by_ts(
            [normalize_parcel(s) for s in active], "planned_from"
        )
