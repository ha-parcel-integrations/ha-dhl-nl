"""DHL Package Tracker custom component for Home Assistant."""
from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DhlApiClient, DhlAuthError
from .const import DOMAIN, PLATFORMS
from .coordinator import DhlCoordinator, DhlSentShipmentsCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DHL from a config entry.

    Obtains the HA-managed aiohttp session, instantiates the API client,
    performs the initial login, and wires up the coordinator.  If login
    fails for any reason (auth or network) ``ConfigEntryNotReady`` is raised
    so that Home Assistant will retry setup automatically.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being set up.

    Returns:
        ``True`` on success.

    Raises:
        ConfigEntryNotReady: If the initial login fails.
    """
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
    except (DhlAuthError, aiohttp.ClientError) as exc:
        raise ConfigEntryNotReady("DHL login failed") from exc

    coordinator = DhlCoordinator(hass, client, entry)
    sent_coordinator = DhlSentShipmentsCoordinator(hass, client)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "sent_coordinator": sent_coordinator,
        "user_info": user_info,
        "session": session,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Refresh the coordinator immediately when options are changed."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a DHL config entry.

    Unloads all platforms and removes the entry's data from
    ``hass.data[DOMAIN]``.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being unloaded.

    Returns:
        ``True`` if all platforms were unloaded successfully.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["session"].close()
    return unload_ok
