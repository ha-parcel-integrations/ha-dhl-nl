"""Coordinator for the DHL Package Tracker integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DhlApiClient, DhlApiError
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
        except (DhlApiError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"DHL error: {err}") from err

        active = filter_active_parcels(raw)
        self.delivered = self._apply_delivered_filter(filter_delivered_parcels(raw))
        _LOGGER.debug(
            "DHL parcels fetched: %d total, %d active, %d delivered",
            len(raw), len(active), len(self.delivered),
        )
        return active

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
        except (DhlApiError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"DHL error (sent): {err}") from err

        active = filter_active_sent_shipments(raw)
        _LOGGER.debug(
            "DHL sent shipments fetched: %d total, %d active", len(raw), len(active)
        )
        return active
