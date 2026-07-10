"""DHL Package Tracker custom component for Home Assistant."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DhlApiClient, DhlApiError, DhlAuthError
from .const import PLATFORMS
from .coordinator import DhlCoordinator, DhlSentShipmentsCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class DhlData:
    """Runtime data attached to a DHL config entry."""

    client: DhlApiClient
    coordinator: DhlCoordinator
    sent_coordinator: DhlSentShipmentsCoordinator
    user_info: dict[str, Any]
    session: aiohttp.ClientSession


type DhlConfigEntry = ConfigEntry[DhlData]


async def async_setup_entry(hass: HomeAssistant, entry: DhlConfigEntry) -> bool:
    """Set up DHL from a config entry."""
    # Each config entry needs its own cookie jar so multiple DHL accounts
    # don't overwrite each other's auth cookies in the shared session.
    session = aiohttp.ClientSession(
        connector=async_get_clientsession(hass).connector,
        connector_owner=False,
        cookie_jar=aiohttp.CookieJar(),
    )
    client = DhlApiClient(
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        session,
    )

    try:
        user_info = await client.async_login()
    except DhlAuthError as exc:
        # Credentials rejected (401/403) — let HA start the reauth flow
        # instead of retrying a password that will never work again.
        await session.close()
        raise ConfigEntryAuthFailed("DHL authentication failed") from exc
    except DhlApiError as exc:
        # Non-auth HTTP failure (typically a 5xx outage) — retry with backoff.
        await session.close()
        raise ConfigEntryNotReady("DHL login failed") from exc
    except aiohttp.ClientError as exc:
        await session.close()
        raise ConfigEntryNotReady("DHL login failed") from exc

    coordinator = DhlCoordinator(hass, client, entry)
    sent_coordinator = DhlSentShipmentsCoordinator(hass, client, entry)

    try:
        # Fetch initial data here, before forwarding to platforms. Raising
        # ConfigEntryNotReady from a forwarded platform is too late for HA to
        # catch cleanly (it logs a warning and leaves half the platforms set
        # up); doing the first refresh here lets a transient failure — e.g. a
        # DHL 429 — fail the whole entry so HA retries it with backoff.
        await coordinator.async_config_entry_first_refresh()
        await sent_coordinator.async_config_entry_first_refresh()
    except Exception:
        # A failed first refresh (ConfigEntryNotReady/ConfigEntryAuthFailed)
        # would otherwise leak the dedicated session on every retry.
        await session.close()
        raise

    entry.runtime_data = DhlData(
        client=client,
        coordinator=coordinator,
        sent_coordinator=sent_coordinator,
        user_info=user_info,
        session=session,
    )

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        # A failed platform setup would otherwise leak the dedicated session
        # on every retry.
        await session.close()
        raise

    return True


async def async_unload_entry(hass: HomeAssistant, entry: DhlConfigEntry) -> bool:
    """Unload a DHL config entry."""
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.session.close()
        return True
    return False
