"""Coordinator for the DHL Package Tracker integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DhlApiClient, DhlApiError, DhlAuthError
from .const import (
    ACTIVE_CATEGORIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DOMAIN,
    POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


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
    """Return a carrier-agnostic parcel dict with the original DHL payload under ``raw``."""
    sender = parcel.get("sender") or {}
    destination = parcel.get("destination") or {}
    delivered = parcel.get("category") == "DELIVERED"
    moment_from, moment_to = _delivery_window(parcel)
    is_pickup = destination.get("locationType") == "SERVICEPOINT"

    return {
        "carrier": "DHL",
        "barcode": parcel.get("barcode"),
        "sender": sender.get("name"),
        "status": parcel.get("status"),
        "delivered": delivered,
        "delivered_at": moment_from if delivered else None,
        "planned_from": None if delivered else moment_from,
        "planned_to": None if delivered else moment_to,
        "pickup": is_pickup,
        "pickup_point": destination.get("name") if is_pickup else None,
        "url": _tracking_url(parcel),
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


class DhlCoordinator(DataUpdateCoordinator[list[dict]]):
    """Coordinator that polls the DHL parcels API on a fixed schedule."""

    def __init__(self, hass: HomeAssistant, client: DhlApiClient, entry: ConfigEntry) -> None:
        """Initialise the coordinator.

        Args:
            hass: The Home Assistant instance.
            client: An authenticated :class:`DhlApiClient` instance.
            entry: The config entry, used to read options for the delivered filter.
        """
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL),
        )
        self._client = client
        self._entry = entry
        self.delivered: list[dict] = []

    async def _async_update_data(self) -> list[dict]:
        try:
            raw = await self._client.async_get_parcels()
        except DhlAuthError as err:
            _LOGGER.error("DHL authentication failed: %s", err)
            raise ConfigEntryAuthFailed("DHL authentication failed") from err
        except (DhlApiError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"DHL error: {err}") from err

        active = filter_active_parcels(raw)
        delivered = self._apply_delivered_filter(filter_delivered_parcels(raw))
        _LOGGER.debug(
            "DHL parcels fetched: %d total, %d active, %d delivered",
            len(raw), len(active), len(delivered),
        )
        self.delivered = [normalize_parcel(p) for p in delivered]
        return [normalize_parcel(p) for p in active]

    def _apply_delivered_filter(self, parcels: list[dict]) -> list[dict]:
        """Apply the configured filter to the delivered parcels list."""
        options = self._entry.options
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

    def __init__(self, hass: HomeAssistant, client: DhlApiClient) -> None:
        """Initialise the coordinator.

        Args:
            hass: The Home Assistant instance.
            client: An authenticated :class:`DhlApiClient` instance.
        """
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_sent",
            update_interval=timedelta(seconds=POLL_INTERVAL),
        )
        self._client = client

    async def _async_update_data(self) -> list[dict]:
        try:
            raw = await self._client.async_get_sent_shipments()
        except DhlAuthError as err:
            _LOGGER.error("DHL authentication failed: %s", err)
            raise ConfigEntryAuthFailed("DHL authentication failed") from err
        except (DhlApiError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"DHL error (sent): {err}") from err

        active = filter_active_sent_shipments(raw)
        _LOGGER.debug(
            "DHL sent shipments fetched: %d total, %d active", len(raw), len(active)
        )
        return [normalize_parcel(s) for s in active]
