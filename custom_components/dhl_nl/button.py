"""Button platform for the DHL Package Tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DhlConfigEntry
from .const import DOMAIN
from .device import build_device_info

# A manual refresh is a single API round-trip; HA's per-entity throttling
# adds nothing here.
PARALLEL_UPDATES = 0




async def async_setup_entry(
    hass: HomeAssistant,
    entry: DhlConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the DHL refresh button from a config entry."""
    async_add_entities([DhlRefreshButton(entry)])


class DhlRefreshButton(ButtonEntity):
    """Button that forces an immediate poll of both DHL coordinators.

    Useful when a parcel is expected and the user does not want to wait for
    the next scheduled refresh. Stateless from HA's perspective.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "refresh"
    _attr_attribution = "Data provided by DHL"

    def __init__(self, entry: DhlConfigEntry) -> None:
        """Initialise the refresh button."""
        self._entry = entry
        user_info = entry.runtime_data.user_info
        user_id: str = user_info.get("userId", "")
        self._attr_unique_id = f"{user_id}_refresh"
        self._attr_device_info = build_device_info(user_info)

    async def async_press(self) -> None:
        """Trigger an immediate refresh of the incoming and sent coordinators."""
        data = self._entry.runtime_data
        await data.coordinator.async_request_refresh()
        await data.sent_coordinator.async_request_refresh()
