"""Sensor platform for the DHL Package Tracker integration."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DhlCoordinator, DhlSentShipmentsCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up DHL sensor entities from a config entry.

    Performs the initial coordinator refreshes, then registers:
    - A summary sensor for incoming active parcels, plus one per-parcel sensor
    - A summary sensor for outgoing active shipments
    """
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: DhlCoordinator = data["coordinator"]
    sent_coordinator: DhlSentShipmentsCoordinator = data["sent_coordinator"]
    user_info: dict[str, Any] = data["user_info"]

    # Perform the first refresh for both coordinators before adding entities.
    await coordinator.async_config_entry_first_refresh()
    await sent_coordinator.async_config_entry_first_refresh()

    entities: list[SensorEntity] = []

    # Incoming parcels — summary + one sensor per parcel + derived sensors.
    summary_sensor = DhlIncomingParcelsSensor(
        coordinator=coordinator,
        user_info=user_info,
        async_add_entities=async_add_entities,
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
    entities.append(DhlPickupPendingSensor(coordinator=coordinator, user_info=user_info))

    # Outgoing shipments — single summary sensor.
    entities.append(
        DhlSentShipmentsSensor(
            coordinator=sent_coordinator,
            user_info=user_info,
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


class DhlIncomingParcelsSensor(CoordinatorEntity[DhlCoordinator], SensorEntity):
    """Summary sensor reporting the count of active incoming DHL parcels.

    Also manages the lifecycle of per-parcel :class:`DhlParcelSensor`
    entities: new barcodes are added and stale barcodes are removed from
    the entity registry whenever the coordinator data changes.
    """

    _attr_name = "DHL Incoming Parcels"
    _attr_icon = "mdi:package-variant-closed"
    _attr_native_unit_of_measurement = "parcels"

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
        self._attr_unique_id = f"{user_id}_incoming_parcels"
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
    """Per-parcel sensor reporting the status of a single incoming DHL shipment."""

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


class DhlSentShipmentsSensor(
    CoordinatorEntity[DhlSentShipmentsCoordinator], SensorEntity
):
    """Summary sensor reporting the count of active outgoing DHL shipments.

    Exposes the full list of in-transit sent shipments as an attribute.
    No per-shipment sensors are created — all data is available on this
    single entity.
    """

    _attr_name = "DHL Outgoing Parcels"
    _attr_icon = "mdi:package-variant-closed"
    _attr_native_unit_of_measurement = "parcels"

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
        shipments = self.coordinator.data or []
        return {
            "shipments": [
                {
                    "barcode": s.get("barcode"),
                    "orderId": s.get("orderId"),
                    "status": s.get("status"),
                    "category": s.get("category"),
                    "receiver": s.get("receiver"),
                    "destination": s.get("destination"),
                    "timeCreated": s.get("timeCreated"),
                    "receivingTimeIndication": s.get("receivingTimeIndication"),
                }
                for s in shipments
            ]
        }


class DhlNextDeliverySensor(CoordinatorEntity[DhlCoordinator], SensorEntity):
    """Sensor reporting the earliest expected delivery datetime across all active parcels.

    State is a timezone-aware datetime (device class TIMESTAMP), which allows
    HA automations to trigger relative to the expected delivery time, e.g.
    "notify me 1 hour before the next delivery".

    Returns ``None`` (unavailable) when there are no active parcels or none
    have a known delivery time indication.
    """

    _attr_name = "DHL Next Delivery"
    _attr_icon = "mdi:clock-fast"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

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
        """Return (datetime, parcel) pairs for all parcels with a known delivery time."""
        result: list[tuple[datetime, dict]] = []
        for parcel in self.coordinator.data or []:
            moment_str: str | None = (parcel.get("receivingTimeIndication") or {}).get("moment")
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
        sender = earliest.get("sender") or {}
        return {
            "barcode": earliest.get("barcode"),
            "sender": sender.get("name"),
        }


class DhlPickupPendingSensor(CoordinatorEntity[DhlCoordinator], SensorEntity):
    """Sensor reporting the number of parcels waiting to be collected at a ServicePoint.

    A parcel is counted when its destination ``locationType`` is ``SERVICEPOINT``
    and its status is not yet ``COLLECTED_AT_PARCELSHOP``. The full list of
    pending pickup parcels is exposed as an attribute.
    """

    _attr_name = "DHL Parcels Awaiting Pickup"
    _attr_icon = "mdi:store-clock"
    _attr_native_unit_of_measurement = "parcels"

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
        """Return active parcels that are waiting at a ServicePoint for collection."""
        return [
            p for p in (self.coordinator.data or [])
            if (p.get("destination") or {}).get("locationType") == "SERVICEPOINT"
            and p.get("status") != "COLLECTED_AT_PARCELSHOP"
        ]

    @property
    def native_value(self) -> int:
        """Return the number of parcels awaiting pickup."""
        return len(self._get_pickup_parcels())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details of each parcel awaiting pickup."""
        parcels = self._get_pickup_parcels()
        return {
            "parcels": [
                {
                    "barcode": p.get("barcode"),
                    "sender": (p.get("sender") or {}).get("name"),
                    "pickup_location": (p.get("destination") or {}).get("name"),
                    "pickup_address": (p.get("destination") or {}).get("address"),
                    "status": p.get("status"),
                }
                for p in parcels
            ]
        }
