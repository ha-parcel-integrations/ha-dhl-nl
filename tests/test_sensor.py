"""Tests for DHL sensor property logic."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.dhl_nl.const import (
    STATUS_AT_SERVICE_POINT,
    ParcelStatus,
)
from custom_components.dhl_nl.coordinator import normalize_parcel
from custom_components.dhl_nl.sensor import (
    DhlDeliveredParcelsSensor,
    DhlEnRouteToServicePointSensor,
    DhlIncomingParcelsSensor,
    DhlNextDeliverySensor,
    DhlOutgoingDeliveredSensor,
    DhlParcelSensor,
    DhlPickupPendingSensor,
    DhlSentShipmentsSensor,
)

USER_INFO = {"userId": "user123", "email": "test@example.com"}


def _make_coordinator(
    parcels: list[dict],
    delivered: list[dict] | None = None,
    returning: list[dict] | None = None,
    delivered_outgoing: list[dict] | None = None,
) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = parcels
    coordinator.delivered = delivered if delivered is not None else []
    coordinator.returning = returning if returning is not None else []
    coordinator.delivered_outgoing = (
        delivered_outgoing if delivered_outgoing is not None else []
    )
    return coordinator


def _parcel(
    barcode: str = "TEST123",
    status: str = "IN_DELIVERY",
    location_type: str = "ADDRESS",
    indication: dict | None = None,
    category: str = "IN_DELIVERY",
) -> dict:
    return normalize_parcel({
        "barcode": barcode,
        "status": status,
        "category": category,
        "destination": {"locationType": location_type, "name": "DHL ServicePoint"},
        "sender": {"name": "Example Sender"},
        "receivingTimeIndication": indication,
    })


# ---------------------------------------------------------------------------
# DhlParcelSensor
# ---------------------------------------------------------------------------

def test_parcel_sensor_returns_normalized_status():
    """Per-parcel sensor state is the canonical ParcelStatus enum value."""
    parcel = _parcel(barcode="ABC", status="OUT_FOR_DELIVERY", category="IN_DELIVERY")
    sensor = DhlParcelSensor(_make_coordinator([parcel]), USER_INFO, "ABC")
    assert sensor.native_value == ParcelStatus.OUT_FOR_DELIVERY


def test_parcel_sensor_exposes_raw_status_in_attributes():
    """The original DHL status string lives on the ``raw_status`` attribute."""
    parcel = _parcel(barcode="ABC", status="OUT_FOR_DELIVERY", category="IN_DELIVERY")
    sensor = DhlParcelSensor(_make_coordinator([parcel]), USER_INFO, "ABC")
    assert sensor.extra_state_attributes["raw_status"] == "OUT_FOR_DELIVERY"
    assert sensor.extra_state_attributes["status"] == ParcelStatus.OUT_FOR_DELIVERY


def test_parcel_sensor_returns_none_when_barcode_missing():
    sensor = DhlParcelSensor(_make_coordinator([_parcel("OTHER")]), USER_INFO, "MISSING")
    assert sensor.native_value is None


def test_parcel_sensor_attributes_contain_full_parcel():
    parcel = _parcel(barcode="ABC")
    sensor = DhlParcelSensor(_make_coordinator([parcel]), USER_INFO, "ABC")
    assert sensor.extra_state_attributes == parcel


# ---------------------------------------------------------------------------
# DhlIncomingParcelsSensor — per-parcel sensor lifecycle
# ---------------------------------------------------------------------------

def test_summary_sensor_removes_stale_per_parcel_entity_from_registry():
    """When a barcode falls out of coordinator data, the summary sensor must
    remove the per-parcel entity from the registry. The previous self-remove
    pattern raced with the coordinator-listener cleanup and could leave a
    ghost entity behind (real-world repro on DHL).
    """
    coordinator = _make_coordinator([_parcel(barcode="A1")])
    add_entities = MagicMock()
    summary = DhlIncomingParcelsSensor(
        coordinator=coordinator,
        user_info=USER_INFO,
        async_add_entities=add_entities,
        known_barcodes={"A1", "A2"},
    )
    summary.hass = MagicMock()

    registry = MagicMock()
    registry.async_get_entity_id.return_value = "sensor.dhl_parcel_a2"

    with patch(
        "custom_components.dhl_nl.sensor.er.async_get",
        return_value=registry,
    ), patch.object(
        DhlIncomingParcelsSensor.__bases__[0], "_handle_coordinator_update"
    ):
        summary._handle_coordinator_update()

    registry.async_get_entity_id.assert_called_once_with(
        "sensor", "dhl_nl", "user123_A2"
    )
    registry.async_remove.assert_called_once_with("sensor.dhl_parcel_a2")


# ---------------------------------------------------------------------------
# DhlNextDeliverySensor — MomentIndication
# ---------------------------------------------------------------------------

def test_next_delivery_moment_indication():
    parcel = _parcel(indication={
        "indicationType": "MomentIndication",
        "moment": "2026-05-20T10:00:00Z",
    })
    sensor = DhlNextDeliverySensor(_make_coordinator([parcel]), USER_INFO)
    result = sensor.native_value
    assert result == datetime(2026, 5, 20, 10, 0, 0, tzinfo=timezone.utc)


def test_next_delivery_interval_indication_uses_start():
    parcel = _parcel(indication={
        "indicationType": "IntervalIndication",
        "start": "2026-05-20T08:00:00Z",
        "end": "2026-05-20T16:00:00Z",
    })
    sensor = DhlNextDeliverySensor(_make_coordinator([parcel]), USER_INFO)
    result = sensor.native_value
    assert result == datetime(2026, 5, 20, 8, 0, 0, tzinfo=timezone.utc)


def test_next_delivery_picks_earliest_of_multiple_parcels():
    parcels = [
        _parcel("A", indication={"indicationType": "MomentIndication", "moment": "2026-05-22T10:00:00Z"}),
        _parcel("B", indication={"indicationType": "MomentIndication", "moment": "2026-05-20T10:00:00Z"}),
    ]
    sensor = DhlNextDeliverySensor(_make_coordinator(parcels), USER_INFO)
    assert sensor.native_value == datetime(2026, 5, 20, 10, 0, 0, tzinfo=timezone.utc)


def test_next_delivery_none_when_no_indication():
    sensor = DhlNextDeliverySensor(_make_coordinator([_parcel()]), USER_INFO)
    assert sensor.native_value is None


def test_next_delivery_none_when_no_parcels():
    sensor = DhlNextDeliverySensor(_make_coordinator([]), USER_INFO)
    assert sensor.native_value is None


def test_next_delivery_skips_unknown_indication_type():
    parcel = _parcel(indication={"indicationType": "UnknownType", "moment": "2026-05-20T10:00:00Z"})
    sensor = DhlNextDeliverySensor(_make_coordinator([parcel]), USER_INFO)
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# DhlEnRouteToServicePointSensor
# ---------------------------------------------------------------------------

def test_en_route_counts_servicepoint_parcels_in_transit():
    parcels = [
        _parcel("A", location_type="SERVICEPOINT", status="IN_DELIVERY"),
        _parcel("B", location_type="ADDRESS", status="IN_DELIVERY"),
    ]
    sensor = DhlEnRouteToServicePointSensor(_make_coordinator(parcels), USER_INFO)
    assert sensor.native_value == 1


def test_en_route_excludes_arrived_at_servicepoint():
    parcel = _parcel(location_type="SERVICEPOINT", status=STATUS_AT_SERVICE_POINT)
    sensor = DhlEnRouteToServicePointSensor(_make_coordinator([parcel]), USER_INFO)
    assert sensor.native_value == 0


def test_en_route_zero_when_no_parcels():
    sensor = DhlEnRouteToServicePointSensor(_make_coordinator([]), USER_INFO)
    assert sensor.native_value == 0


# ---------------------------------------------------------------------------
# DhlPickupPendingSensor
# ---------------------------------------------------------------------------

def test_pickup_pending_counts_arrived_parcels():
    parcel = _parcel(location_type="SERVICEPOINT", status=STATUS_AT_SERVICE_POINT)
    sensor = DhlPickupPendingSensor(_make_coordinator([parcel]), USER_INFO)
    assert sensor.native_value == 1


def test_pickup_pending_excludes_in_transit_servicepoint():
    parcel = _parcel(location_type="SERVICEPOINT", status="IN_DELIVERY")
    sensor = DhlPickupPendingSensor(_make_coordinator([parcel]), USER_INFO)
    assert sensor.native_value == 0


def test_pickup_pending_excludes_home_address_parcels():
    parcel = _parcel(location_type="ADDRESS", status=STATUS_AT_SERVICE_POINT)
    sensor = DhlPickupPendingSensor(_make_coordinator([parcel]), USER_INFO)
    assert sensor.native_value == 0


def test_pickup_pending_zero_when_no_parcels():
    sensor = DhlPickupPendingSensor(_make_coordinator([]), USER_INFO)
    assert sensor.native_value == 0


# ---------------------------------------------------------------------------
# DhlDeliveredParcelsSensor
# ---------------------------------------------------------------------------


def _delivered_parcel(barcode: str = "DEL123") -> dict:
    return normalize_parcel({
        "barcode": barcode,
        "category": "DELIVERED",
        "isReturn": False,
        "status": "DELIVERED",
        "sender": {"name": "Test Sender"},
        "receivingTimeIndication": {"indicationType": "MomentIndication", "moment": "2026-05-30T14:00:00Z"},
    })


def test_delivered_sensor_count_matches_coordinator_delivered():
    delivered = [_delivered_parcel("A"), _delivered_parcel("B")]
    sensor = DhlDeliveredParcelsSensor(_make_coordinator([], delivered), USER_INFO)
    assert sensor.native_value == 2


def test_delivered_sensor_zero_when_no_delivered():
    sensor = DhlDeliveredParcelsSensor(_make_coordinator([]), USER_INFO)
    assert sensor.native_value == 0


def test_delivered_sensor_attributes_list_parcels():
    delivered = [_delivered_parcel("DEL1")]
    sensor = DhlDeliveredParcelsSensor(_make_coordinator([], delivered), USER_INFO)
    attrs = sensor.extra_state_attributes
    assert "parcels" in attrs
    assert len(attrs["parcels"]) == 1
    assert attrs["parcels"][0]["barcode"] == "DEL1"


def test_delivered_sensor_attributes_include_sender():
    delivered = [_delivered_parcel("DEL1")]
    sensor = DhlDeliveredParcelsSensor(_make_coordinator([], delivered), USER_INFO)
    attrs = sensor.extra_state_attributes
    assert attrs["parcels"][0]["sender"] == "Test Sender"


def test_delivered_sensor_attributes_handle_missing_sender():
    parcel = normalize_parcel({
        "barcode": "DEL123",
        "category": "DELIVERED",
        "isReturn": False,
        "status": "DELIVERED",
        "sender": None,
        "receivingTimeIndication": {"indicationType": "MomentIndication", "moment": "2026-05-30T14:00:00Z"},
    })
    sensor = DhlDeliveredParcelsSensor(_make_coordinator([], [parcel]), USER_INFO)
    attrs = sensor.extra_state_attributes
    assert attrs["parcels"][0]["sender"] is None


# ---------------------------------------------------------------------------
# DhlSentShipmentsSensor / DhlOutgoingDeliveredSensor — merge sent shipments
# with return parcels into a single "outgoing" concept.
# ---------------------------------------------------------------------------


def _return_parcel(barcode: str = "RET123", category: str = "UNDERWAY") -> dict:
    return normalize_parcel({
        "barcode": barcode,
        "category": category,
        "isReturn": True,
        "status": "PARCEL_READY_FOR_RETURN_TO_HUB",
        "sender": {"name": "Test User"},
        "receiver": {"name": "AE-RTN-NL"},
    })


def _sent_shipment(barcode: str = "SENT123") -> dict:
    return normalize_parcel({
        "barcode": barcode,
        "category": "IN_DELIVERY",
        "sender": {"name": "Test User"},
    })


def _make_sent_coordinator(data=None, delivered=None) -> MagicMock:
    sent_coordinator = MagicMock()
    sent_coordinator.data = data if data is not None else []
    sent_coordinator.delivered = delivered if delivered is not None else []
    return sent_coordinator


def test_outgoing_sensor_counts_returns_only_when_sent_coordinator_empty():
    """The common case: the sent-shipments endpoint is empty, returns are not."""
    coordinator = _make_coordinator([], returning=[_return_parcel("A"), _return_parcel("B")])
    sensor = DhlSentShipmentsSensor(coordinator, _make_sent_coordinator(), USER_INFO)
    assert sensor.native_value == 2


def test_outgoing_sensor_merges_sent_shipments_and_returns():
    coordinator = _make_coordinator([], returning=[_return_parcel("A")])
    sent_coordinator = _make_sent_coordinator(data=[_sent_shipment("B")])
    sensor = DhlSentShipmentsSensor(coordinator, sent_coordinator, USER_INFO)
    assert sensor.native_value == 2
    barcodes = {p["barcode"] for p in sensor.extra_state_attributes["parcels"]}
    assert barcodes == {"A", "B"}


def test_outgoing_sensor_zero_when_both_sources_empty():
    sensor = DhlSentShipmentsSensor(_make_coordinator([]), _make_sent_coordinator(), USER_INFO)
    assert sensor.native_value == 0


def test_outgoing_delivered_sensor_counts_delivered_returns_only_when_sent_empty():
    delivered_outgoing = [
        _return_parcel("A", category="DELIVERED"),
        _return_parcel("B", category="DELIVERED"),
    ]
    coordinator = _make_coordinator([], delivered_outgoing=delivered_outgoing)
    sensor = DhlOutgoingDeliveredSensor(coordinator, _make_sent_coordinator(), USER_INFO)
    assert sensor.native_value == 2


def test_outgoing_delivered_sensor_merges_both_sources():
    coordinator = _make_coordinator([], delivered_outgoing=[_return_parcel("A", category="DELIVERED")])
    sent_coordinator = _make_sent_coordinator(delivered=[_sent_shipment("B")])
    sensor = DhlOutgoingDeliveredSensor(coordinator, sent_coordinator, USER_INFO)
    assert sensor.native_value == 2
    barcodes = {p["barcode"] for p in sensor.extra_state_attributes["parcels"]}
    assert barcodes == {"A", "B"}


def test_outgoing_delivered_sensor_zero_when_both_sources_empty():
    sensor = DhlOutgoingDeliveredSensor(_make_coordinator([]), _make_sent_coordinator(), USER_INFO)
    assert sensor.native_value == 0


def test_outgoing_delivered_sensor_attributes_include_delivered_flag():
    coordinator = _make_coordinator([], delivered_outgoing=[_return_parcel("RET2", category="DELIVERED")])
    sensor = DhlOutgoingDeliveredSensor(coordinator, _make_sent_coordinator(), USER_INFO)
    attrs = sensor.extra_state_attributes
    assert attrs["parcels"][0]["barcode"] == "RET2"
    assert attrs["parcels"][0]["delivered"] is True


@pytest.mark.parametrize("sensor_cls", [DhlSentShipmentsSensor, DhlOutgoingDeliveredSensor])
async def test_outgoing_sensor_also_subscribes_to_sent_coordinator(sensor_cls):
    """Each outgoing sensor reads from both coordinators, so it must also
    listen to the sent-shipments coordinator, not just the main one."""
    sent_coordinator = _make_sent_coordinator()
    sensor = sensor_cls(_make_coordinator([]), sent_coordinator, USER_INFO)
    sensor.async_on_remove = MagicMock()
    with patch(
        "custom_components.dhl_nl.sensor.CoordinatorEntity.async_added_to_hass",
        AsyncMock(),
    ):
        await sensor.async_added_to_hass()
    sent_coordinator.async_add_listener.assert_called_once_with(sensor.async_write_ha_state)
    sensor.async_on_remove.assert_called_once()


# ---------------------------------------------------------------------------
# DhlLastUpdateSensor
# ---------------------------------------------------------------------------


def test_last_update_sensor_reports_coordinator_timestamp():
    from datetime import datetime, timezone

    from custom_components.dhl_nl.sensor import DhlLastUpdateSensor

    coordinator = _make_coordinator([])
    moment = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    coordinator.last_success_time = moment
    sensor = DhlLastUpdateSensor(coordinator, USER_INFO)
    assert sensor.native_value == moment


def test_last_update_sensor_none_before_first_success():
    from custom_components.dhl_nl.sensor import DhlLastUpdateSensor

    coordinator = _make_coordinator([])
    coordinator.last_success_time = None
    sensor = DhlLastUpdateSensor(coordinator, USER_INFO)
    assert sensor.native_value is None
