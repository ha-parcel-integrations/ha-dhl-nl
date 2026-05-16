"""Sensor platform for the DHL Package Tracker integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DhlCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up DHL sensor entities from a config entry.

    Performs the initial coordinator refresh, then registers the summary
    sensor and one per-parcel sensor for every active parcel.
    """
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: DhlCoordinator = data["coordinator"]
    user_info: dict[str, Any] = data["user_info"]

    # Perform the first refresh before adding entities (Requirement 6.2).
    await coordinator.async_config_entry_first_refresh()

    entities: list[SensorEntity] = []

    summary_sensor = DhlPackagesSensor(
        coordinator=coordinator,
        user_info=user_info,
        async_add_entities=async_add_entities,
    )
    entities.append(summary_sensor)

    # Create one DhlParcelSensor per active parcel in the initial data.
    for parcel in coordinator.data or []:
        barcode = parcel.get("barcode", "")
        entities.append(
            DhlParcelSensor(
                coordinator=coordinator,
                user_info=user_info,
                barcode=barcode,
            )
        )

    async_add_entities(entities)


def _build_device_info(user_info: dict[str, Any]) -> DeviceInfo:
    """Return a DeviceInfo dict shared by all sensors for this account."""
    user_id: str = user_info.get("userId", "")
    email: str = user_info.get("email", "")
    return DeviceInfo(
        identifiers={(DOMAIN, user_id)},
        name=email,
        manufacturer="DHL",
    )


class DhlPackagesSensor(CoordinatorEntity[DhlCoordinator], SensorEntity):
    """Summary sensor reporting the count of active DHL parcels.

    Also manages the lifecycle of per-parcel :class:`DhlParcelSensor`
    entities: new barcodes are added and stale barcodes are removed from
    the entity registry whenever the coordinator data changes.
    """

    _attr_name = "DHL Packages"
    _attr_icon = "mdi:package-variant-closed"
    _attr_native_unit_of_measurement = "packages"

    def __init__(
        self,
        coordinator: DhlCoordinator,
        user_info: dict[str, Any],
        async_add_entities: AddEntitiesCallback,
    ) -> None:
        """Initialise the summary sensor."""
        super().__init__(coordinator)
        self._user_info = user_info
        self._async_add_entities = async_add_entities
        user_id: str = user_info.get("userId", "")
        self._attr_unique_id = f"{user_id}_packages"
        self._attr_device_info = _build_device_info(user_info)
        # Track which barcodes already have a DhlParcelSensor registered.
        self._known_barcodes: set[str] = set()

    # ------------------------------------------------------------------
    # SensorEntity interface
    # ------------------------------------------------------------------

    @property
    def native_value(self) -> int:
        """Return the number of active parcels."""
        return len(self.coordinator.data or [])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full list of active parcels as an attribute."""
        return {"parcels": self.coordinator.data or []}

    # ------------------------------------------------------------------
    # Coordinator update hook
    # ------------------------------------------------------------------

    def _handle_coordinator_update(self) -> None:
        """Reconcile per-parcel sensors and trigger a state write."""
        current_parcels: list[dict] = self.coordinator.data or []
        current_barcodes: set[str] = {
            p.get("barcode", "") for p in current_parcels
        }

        # Add sensors for barcodes that are new.
        new_barcodes = current_barcodes - self._known_barcodes
        if new_barcodes:
            new_entities = [
                DhlParcelSensor(
                    coordinator=self.coordinator,
                    user_info=self._user_info,
                    barcode=barcode,
                )
                for barcode in new_barcodes
            ]
            self._async_add_entities(new_entities)

        # Remove sensors for barcodes that are no longer active.
        stale_barcodes = self._known_barcodes - current_barcodes
        if stale_barcodes and self.hass is not None:
            registry = er.async_get(self.hass)
            user_id: str = self._user_info.get("userId", "")
            for barcode in stale_barcodes:
                unique_id = f"{user_id}_{barcode}"
                entity_id = registry.async_get_entity_id(
                    "sensor", DOMAIN, unique_id
                )
                if entity_id:
                    registry.async_remove(entity_id)

        self._known_barcodes = current_barcodes

        # Trigger the normal HA state write.
        super()._handle_coordinator_update()


class DhlParcelSensor(CoordinatorEntity[DhlCoordinator], SensorEntity):
    """Per-parcel sensor reporting the status of a single DHL shipment."""

    _attr_icon = "mdi:package-variant-closed"

    def __init__(
        self,
        coordinator: DhlCoordinator,
        user_info: dict[str, Any],
        barcode: str,
    ) -> None:
        """Initialise the per-parcel sensor."""
        super().__init__(coordinator)
        self._user_info = user_info
        self._barcode = barcode
        user_id: str = user_info.get("userId", "")
        self._attr_unique_id = f"{user_id}_{barcode}"
        self._attr_name = f"DHL Parcel {barcode}"
        self._attr_device_info = _build_device_info(user_info)

    # ------------------------------------------------------------------
    # SensorEntity interface
    # ------------------------------------------------------------------

    def _get_parcel(self) -> dict[str, Any] | None:
        """Find this sensor's parcel in the coordinator data."""
        for parcel in self.coordinator.data or []:
            if parcel.get("barcode") == self._barcode:
                return parcel
        return None

    @property
    def native_value(self) -> str | None:
        """Return the parcel status string."""
        parcel = self._get_parcel()
        return parcel.get("status") if parcel else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full parcel dict as attributes."""
        parcel = self._get_parcel()
        return dict(parcel) if parcel else {}
