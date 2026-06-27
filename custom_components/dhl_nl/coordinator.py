"""Coordinator for the DHL Package Tracker integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DhlApiClient, DhlApiError, DhlAuthError
from .const import (
    ACTIVE_CATEGORIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_REFRESH_INTERVAL,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
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

# Already-logged raw statuses so we surface each unmapped value only once
# per HA session.
_unmapped_statuses_logged: set[tuple[str, str]] = set()


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
        _LOGGER.info(
            "DHL parcel status not yet mapped: status=%r category=%r — "
            "will report as ParcelStatus.UNKNOWN. Please open an issue "
            "so we can add it to the map.",
            raw_status,
            raw_category,
        )
    return ParcelStatus.UNKNOWN


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


def normalize_parcel(parcel: dict) -> dict:
    """Return a carrier-agnostic parcel dict with the original DHL payload under ``raw``.

    ``weight`` and ``dimensions`` are part of the canonical shape every carrier
    integration publishes but DHL does not expose them in any endpoint we know
    of, so they are always ``None`` here. Aggregator and cross-carrier cards
    can still rely on the keys being present.
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
        # barcode -> last seen ParcelStatus. ``None`` on the first refresh so
        # we can suppress events for parcels that already existed when the
        # integration started (we do not know their previous state).
        self._known_state: dict[str, ParcelStatus] | None = None
        # barcode -> last seen (planned_from, planned_to) tuple. Mirrors
        # ``_known_state`` for delivery-time-change detection.
        self._known_delivery_times: (
            dict[str, tuple[str | None, str | None]] | None
        ) = None

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
        _LOGGER.debug(
            "DHL parcels fetched: %d total, %d active, %d delivered",
            len(raw), len(active), len(delivered),
        )
        self.delivered = sort_parcels_by_ts(
            [normalize_parcel(p) for p in delivered],
            "delivered_at",
            descending=True,
        )
        normalized_active = sort_parcels_by_ts(
            [normalize_parcel(p) for p in active], "planned_from"
        )

        self._fire_change_events(normalized_active)

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

        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode:
                continue
            new_status = parcel["status"]
            if barcode not in self._known_state:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_parcel_registered",
                    {**parcel},
                )
                continue

            if self._known_state[barcode] != new_status:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_parcel_status_changed",
                    {
                        **parcel,
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
                        "old_planned_from": old_from,
                        "new_planned_from": new_from,
                        "old_planned_to": old_to,
                        "new_planned_to": new_to,
                    },
                )

    def _apply_delivered_filter(self, parcels: list[dict]) -> list[dict]:
        """Apply the configured filter to the delivered parcels list."""
        options = self.config_entry.options
        filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
        filter_amount = int(options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT))

        if filter_type == "days":
            cutoff = datetime.now(timezone.utc) - timedelta(days=filter_amount)
            return [p for p in parcels if self._delivery_dt(p) is None or self._delivery_dt(p) >= cutoff]

        # "parcels" — return the most recent N
        return parcels[:filter_amount]

    @staticmethod
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

    async def _async_update_data(self) -> list[dict]:
        try:
            raw = await self._client.async_get_sent_shipments()
        except DhlAuthError as err:
            raise ConfigEntryAuthFailed("DHL authentication failed") from err
        except DhlApiError as err:
            raise UpdateFailed(f"DHL error (sent): {err}") from err
        # aiohttp.ClientError is wrapped automatically by DataUpdateCoordinator.

        active = filter_active_sent_shipments(raw)
        _LOGGER.debug(
            "DHL sent shipments fetched: %d total, %d active", len(raw), len(active)
        )
        return sort_parcels_by_ts(
            [normalize_parcel(s) for s in active], "planned_from"
        )
