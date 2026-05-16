"""Coordinator for the DHL Package Tracker integration."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DhlApiClient, DhlApiError
from .const import ACTIVE_CATEGORIES, DOMAIN, POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)


def filter_active_parcels(parcels: list[dict]) -> list[dict]:
    """Return only active incoming parcels (not returns, in an active category)."""
    return [
        p for p in parcels
        if not p.get("isReturn", True)
        and p.get("category") in ACTIVE_CATEGORIES
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

    def __init__(self, hass: HomeAssistant, client: DhlApiClient) -> None:
        """Initialise the coordinator.

        Args:
            hass: The Home Assistant instance.
            client: An authenticated :class:`DhlApiClient` instance.
        """
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL),
        )
        self._client = client

    async def _async_update_data(self) -> list[dict]:
        """Fetch parcels from the DHL API, filter, and return active ones.

        Re-authenticates once on HTTP 401/403 before retrying.

        Raises:
            UpdateFailed: On any API or network error that prevents a
                          successful update.
        """
        try:
            raw = await self._client.async_get_parcels()
        except DhlApiError as err:
            if err.status_code in (401, 403):
                # Session expired — re-login once and retry.
                try:
                    await self._client.async_login()
                    raw = await self._client.async_get_parcels()
                except DhlApiError as retry_err:
                    msg = f"DHL API error after re-auth: status {retry_err.status_code}"
                    _LOGGER.error(msg)
                    raise UpdateFailed(msg) from retry_err
                except aiohttp.ClientError as retry_net_err:
                    msg = f"DHL network error after re-auth: {retry_net_err}"
                    _LOGGER.error(msg)
                    raise UpdateFailed(msg) from retry_net_err
            else:
                msg = f"DHL API error: status {err.status_code}"
                _LOGGER.error(msg)
                raise UpdateFailed(msg) from err
        except aiohttp.ClientError as net_err:
            msg = f"DHL network error: {net_err}"
            _LOGGER.error(msg)
            raise UpdateFailed(msg) from net_err

        active = filter_active_parcels(raw)
        _LOGGER.debug(
            "DHL parcels fetched: %d total, %d active", len(raw), len(active)
        )
        return active


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
        """Fetch sent shipments from the DHL API, filter, and return active ones.

        Re-authenticates once on HTTP 401/403 before retrying.

        Raises:
            UpdateFailed: On any API or network error that prevents a
                          successful update.
        """
        try:
            raw = await self._client.async_get_sent_shipments()
        except DhlApiError as err:
            if err.status_code in (401, 403):
                try:
                    await self._client.async_login()
                    raw = await self._client.async_get_sent_shipments()
                except DhlApiError as retry_err:
                    msg = f"DHL API error after re-auth (sent): status {retry_err.status_code}"
                    _LOGGER.error(msg)
                    raise UpdateFailed(msg) from retry_err
                except aiohttp.ClientError as retry_net_err:
                    msg = f"DHL network error after re-auth (sent): {retry_net_err}"
                    _LOGGER.error(msg)
                    raise UpdateFailed(msg) from retry_net_err
            else:
                msg = f"DHL API error (sent): status {err.status_code}"
                _LOGGER.error(msg)
                raise UpdateFailed(msg) from err
        except aiohttp.ClientError as net_err:
            msg = f"DHL network error (sent): {net_err}"
            _LOGGER.error(msg)
            raise UpdateFailed(msg) from net_err

        active = filter_active_sent_shipments(raw)
        _LOGGER.debug(
            "DHL sent shipments fetched: %d total, %d active", len(raw), len(active)
        )
        return active
