"""Sensor platform for the DHL Package Tracker integration."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DhlConfigEntry
from .const import DOMAIN, ParcelStatus
from .coordinator import DhlCoordinator, DhlSentShipmentsCoordinator

_LOGGER = logging.getLogger(__name__)

# The DataUpdateCoordinator handles fan-out to all entities; HA's per-entity
# update throttling adds nothing here.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DhlConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up DHL sensor entities from a config entry.

    Performs the initial coordinator refreshes, then registers:
    - A summary sensor for incoming active parcels, plus one per-parcel sensor
    - A summary sensor for outgoing active shipments
    """
    data = entry.runtime_data
    coordinator = data.coordinator
    sent_coordinator = data.sent_coordinator
    user_info = data.user_info

    # Perform the first refresh for both coordinators before adding entities.
    await coordinator.async_config_entry_first_refresh()
    await sent_coordinator.async_config_entry_first_refresh()

    current_barcodes: set[str] = {
        p.get("barcode", "") for p in coordinator.data or []
    }
    user_id: str = user_info.get("userId", "")

    # Remove per-parcel sensors from the entity registry that are no longer
    # active — handles parcels that were delivered between HA restarts.
    registry = er.async_get(hass)
    non_parcel_unique_ids = {
        f"{user_id}_incoming_parcels",
        f"{user_id}_next_delivery",
        f"{user_id}_pickup_pending",
        f"{user_id}_en_route_to_service_point",
        f"{user_id}_outgoing_parcels",
        f"{user_id}_delivered_parcels",
    }
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity_entry.unique_id.startswith(f"{user_id}_")
            and entity_entry.unique_id not in non_parcel_unique_ids
        ):
            barcode = entity_entry.unique_id[len(f"{user_id}_"):]
            if barcode not in current_barcodes:
                registry.async_remove(entity_entry.entity_id)

    entities: list[SensorEntity] = []

    # Incoming parcels — summary + one sensor per parcel + derived sensors.
    summary_sensor = DhlIncomingParcelsSensor(
        coordinator=coordinator,
        user_info=user_info,
        async_add_entities=async_add_entities,
        known_barcodes=current_barcodes,
    )
    entities.append(summary_sensor)

    for parcel in coordinator.data or []:
        barcode = parcel.get("barcode", "")
        entities.append(
            DhlParcelSensor(
                coordinator=coordinator,
                user_info=user_info,
                barcode=barcode,
            )
        )

    entities.append(DhlNextDeliverySensor(coordinator=coordinator, user_info=user_info))
    entities.append(DhlEnRouteToServicePointSensor(coordinator=coordinator, user_info=user_info))
    entities.append(DhlPickupPendingSensor(coordinator=coordinator, user_info=user_info))
    entities.append(DhlDeliveredParcelsSensor(coordinator=coordinator, user_info=user_info))

    # Outgoing shipments — single summary sensor.
    entities.append(
        DhlSentShipmentsSensor(
            coordinator=sent_coordinator,
            user_info=user_info,
        )
    )

    async_add_entities(entities)


def _build_device_info(user_info: dict[str, Any]) -> DeviceInfo:
    """Return a DeviceInfo dict shared by all sensors for this account.

    Device name is the integration ("DHL") rather than the account email
    so the auto-prefixed entity friendly names read as "DHL Incoming
    parcels" instead of "user@example.com Incoming parcels". Users with
    multiple DHL accounts can rename the device in the HA UI to
    distinguish.
    """
    user_id: str = user_info.get("userId", "")
    return DeviceInfo(
        identifiers={(DOMAIN, user_id)},
        name="DHL",
        manufacturer="DHL",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://my.dhlecommerce.nl",
    )


class DhlIncomingParcelsSensor(CoordinatorEntity[DhlCoordinator], SensorEntity):
    """Summary sensor reporting the count of active incoming DHL parcels.

    Spawns a per-parcel :class:`DhlParcelSensor` whenever a new barcode
    appears. Stale per-parcel sensors remove themselves once their barcode
    drops out of the coordinator data — see ``DhlParcelSensor``.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "incoming_parcels"
    _attr_icon = "mdi:package-variant-closed"
    _attr_native_unit_of_measurement = "parcels"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_attribution = "Data provided by DHL"
    _unrecorded_attributes = frozenset({"parcels"})

    def __init__(
        self,
        coordinator: DhlCoordinator,
        user_info: dict[str, Any],
        async_add_entities: AddEntitiesCallback,
        known_barcodes: set[str] | None = None,
    ) -> None:
        """Initialise the summary sensor."""
        super().__init__(coordinator)
        self._user_info = user_info
        self._async_add_entities = async_add_entities
        user_id: str = user_info.get("userId", "")
        self._attr_unique_id = f"{user_id}_incoming_parcels"
        self._attr_device_info = _build_device_info(user_info)
        self._known_barcodes: set[str] = known_barcodes or set()

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
        """Spawn per-parcel sensors for new barcodes and trigger a state write."""
        current_barcodes: set[str] = {
            p.get("barcode", "") for p in (self.coordinator.data or [])
        }

        new_barcodes = current_barcodes - self._known_barcodes
        if new_barcodes:
            self._async_add_entities(
                DhlParcelSensor(
                    coordinator=self.coordinator,
                    user_info=self._user_info,
                    barcode=barcode,
                )
                for barcode in new_barcodes
            )

        self._known_barcodes = current_barcodes
        super()._handle_coordinator_update()


class DhlParcelSensor(CoordinatorEntity[DhlCoordinator], SensorEntity):
    """Per-parcel sensor reporting the status of a single incoming DHL shipment."""

    _attr_has_entity_name = True
    _attr_translation_key = "parcel"
    _attr_icon = "mdi:package-variant-closed"
    _attr_attribution = "Data provided by DHL"

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
        self._attr_translation_placeholders = {"barcode": barcode}
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

    def _handle_coordinator_update(self) -> None:
        """Self-remove once this parcel falls out of the coordinator data."""
        if self._get_parcel() is None and self.hass is not None:
            self.hass.async_create_task(self.async_remove(force_remove=True))
            return
        super()._handle_coordinator_update()


class DhlSentShipmentsSensor(
    CoordinatorEntity[DhlSentShipmentsCoordinator], SensorEntity
):
    """Summary sensor reporting the count of active outgoing DHL shipments.

    Exposes the full list of in-transit sent shipments as an attribute.
    No per-shipment sensors are created — all data is available on this
    single entity.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "outgoing_parcels"
    _attr_icon = "mdi:package-variant-closed"
    _attr_native_unit_of_measurement = "parcels"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_attribution = "Data provided by DHL"
    _unrecorded_attributes = frozenset({"shipments"})

    def __init__(
        self,
        coordinator: DhlSentShipmentsCoordinator,
        user_info: dict[str, Any],
    ) -> None:
        """Initialise the sent shipments sensor."""
        super().__init__(coordinator)
        self._user_info = user_info
        user_id: str = user_info.get("userId", "")
        self._attr_unique_id = f"{user_id}_outgoing_parcels"
        self._attr_device_info = _build_device_info(user_info)

    # ------------------------------------------------------------------
    # SensorEntity interface
    # ------------------------------------------------------------------

    @property
    def native_value(self) -> int:
        """Return the number of active outgoing shipments."""
        return len(self.coordinator.data or [])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full list of active sent shipments as an attribute."""
        return {"shipments": self.coordinator.data or []}


class DhlNextDeliverySensor(CoordinatorEntity[DhlCoordinator], SensorEntity):
    """Sensor reporting the earliest expected delivery datetime across all active parcels.

    State is a timezone-aware datetime (device class TIMESTAMP), which allows
    HA automations to trigger relative to the expected delivery time, e.g.
    "notify me 1 hour before the next delivery".

    Returns ``None`` (unavailable) when there are no active parcels or none
    have a known delivery time indication.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "next_delivery"
    _attr_icon = "mdi:clock-fast"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_attribution = "Data provided by DHL"

    def __init__(
        self,
        coordinator: DhlCoordinator,
        user_info: dict[str, Any],
    ) -> None:
        """Initialise the next delivery sensor."""
        super().__init__(coordinator)
        self._user_info = user_info
        user_id: str = user_info.get("userId", "")
        self._attr_unique_id = f"{user_id}_next_delivery"
        self._attr_device_info = _build_device_info(user_info)

    def _delivery_moments(self) -> list[tuple[datetime, dict]]:
        """Return (datetime, parcel) pairs for parcels with a known ``planned_from``."""
        result: list[tuple[datetime, dict]] = []
        for parcel in self.coordinator.data or []:
            moment_str = parcel.get("planned_from")
            if not moment_str:
                continue
            try:
                dt = datetime.fromisoformat(moment_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                result.append((dt, parcel))
            except ValueError:
                _LOGGER.debug("Could not parse delivery moment: %s", moment_str)
        return result

    @property
    def native_value(self) -> datetime | None:
        """Return the earliest expected delivery datetime across active parcels."""
        moments = self._delivery_moments()
        return min(dt for dt, _ in moments) if moments else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the barcode and sender of the parcel with the earliest delivery."""
        moments = self._delivery_moments()
        if not moments:
            return {}
        _, earliest = min(moments, key=lambda x: x[0])
        return {
            "barcode": earliest.get("barcode"),
            "sender": earliest.get("sender"),
        }


class DhlEnRouteToServicePointSensor(CoordinatorEntity[DhlCoordinator], SensorEntity):
    """Sensor reporting parcels still in transit to a DHL ServicePoint.

    A parcel is counted when its destination ``locationType`` is ``SERVICEPOINT``
    and it has not yet arrived. The filter will be tightened once the exact
    arrived-at-ServicePoint status value is known — for now all ServicePoint-
    destined active parcels are included.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "en_route_to_service_point"
    _attr_icon = "mdi:truck-delivery"
    _attr_native_unit_of_measurement = "parcels"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_attribution = "Data provided by DHL"
    _unrecorded_attributes = frozenset({"parcels"})

    def __init__(
        self,
        coordinator: DhlCoordinator,
        user_info: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._user_info = user_info
        user_id: str = user_info.get("userId", "")
        self._attr_unique_id = f"{user_id}_en_route_to_service_point"
        self._attr_device_info = _build_device_info(user_info)

    def _get_en_route_parcels(self) -> list[dict]:
        """Return active parcels still in transit to a ServicePoint."""
        return [
            p for p in (self.coordinator.data or [])
            if p.get("pickup")
            and p.get("status") != ParcelStatus.AT_PICKUP_POINT
        ]

    @property
    def native_value(self) -> int:
        return len(self._get_en_route_parcels())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"parcels": self._get_en_route_parcels()}


class DhlPickupPendingSensor(CoordinatorEntity[DhlCoordinator], SensorEntity):
    """Sensor reporting the number of parcels waiting to be collected at a ServicePoint.

    A parcel is counted when its destination ``locationType`` is ``SERVICEPOINT``
    and its status is not yet ``COLLECTED_AT_PARCELSHOP``. The full list of
    pending pickup parcels is exposed as an attribute.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "awaiting_pickup"
    _attr_icon = "mdi:store-clock"
    _attr_native_unit_of_measurement = "parcels"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_attribution = "Data provided by DHL"
    _unrecorded_attributes = frozenset({"parcels"})

    def __init__(
        self,
        coordinator: DhlCoordinator,
        user_info: dict[str, Any],
    ) -> None:
        """Initialise the pickup pending sensor."""
        super().__init__(coordinator)
        self._user_info = user_info
        user_id: str = user_info.get("userId", "")
        self._attr_unique_id = f"{user_id}_pickup_pending"
        self._attr_device_info = _build_device_info(user_info)

    def _get_pickup_parcels(self) -> list[dict]:
        """Return parcels that have arrived at a ServicePoint and are ready for collection."""
        return [
            p for p in (self.coordinator.data or [])
            if p.get("pickup")
            and p.get("status") == ParcelStatus.AT_PICKUP_POINT
        ]

    @property
    def native_value(self) -> int:
        """Return the number of parcels awaiting pickup."""
        return len(self._get_pickup_parcels())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details of each parcel awaiting pickup."""
        return {"parcels": self._get_pickup_parcels()}


class DhlDeliveredParcelsSensor(CoordinatorEntity[DhlCoordinator], SensorEntity):
    """Sensor reporting recently delivered incoming DHL parcels."""

    _attr_has_entity_name = True
    _attr_translation_key = "delivered_parcels"
    _attr_icon = "mdi:package-variant"
    _attr_native_unit_of_measurement = "parcels"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_attribution = "Data provided by DHL"
    _unrecorded_attributes = frozenset({"parcels"})

    def __init__(
        self,
        coordinator: DhlCoordinator,
        user_info: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._user_info = user_info
        user_id: str = user_info.get("userId", "")
        self._attr_unique_id = f"{user_id}_delivered_parcels"
        self._attr_device_info = _build_device_info(user_info)

    @property
    def native_value(self) -> int:
        return len(self.coordinator.delivered)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"parcels": self.coordinator.delivered}
