"""Tests for DHL sensor property logic."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.dhl_nl.const import STATUS_AT_SERVICE_POINT
from custom_components.dhl_nl.sensor import (
    DhlDeliveredParcelsSensor,
    DhlEnRouteToServicePointSensor,
    DhlNextDeliverySensor,
    DhlParcelSensor,
    DhlPickupPendingSensor,
)

USER_INFO = {"userId": "user123", "email": "test@example.com"}


def _make_coordinator(parcels: list[dict], delivered: list[dict] | None = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = parcels
    coordinator.delivered = delivered if delivered is not None else []
    return coordinator


def _parcel(
    barcode: str = "TEST123",
    status: str = "IN_DELIVERY",
    location_type: str = "ADDRESS",
    indication: dict | None = None,
) -> dict:
    return {
        "barcode": barcode,
        "status": status,
        "category": "IN_DELIVERY",
        "destination": {"locationType": location_type, "name": "DHL ServicePoint"},
        "sender": {"name": "Example Sender"},
        "receivingTimeIndication": indication,
    }


# ---------------------------------------------------------------------------
# DhlParcelSensor
# ---------------------------------------------------------------------------

def test_parcel_sensor_returns_status():
    parcel = _parcel(barcode="ABC", status="DELIVERED_IN_MAILBOX")
    sensor = DhlParcelSensor(_make_coordinator([parcel]), USER_INFO, "ABC")
    assert sensor.native_value == "DELIVERED_IN_MAILBOX"


def test_parcel_sensor_returns_none_when_barcode_missing():
    sensor = DhlParcelSensor(_make_coordinator([_parcel("OTHER")]), USER_INFO, "MISSING")
    assert sensor.native_value is None


def test_parcel_sensor_attributes_contain_full_parcel():
    parcel = _parcel(barcode="ABC")
    sensor = DhlParcelSensor(_make_coordinator([parcel]), USER_INFO, "ABC")
    assert sensor.extra_state_attributes == parcel


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
    return {
        "barcode": barcode,
        "category": "DELIVERED",
        "isReturn": False,
        "status": "DELIVERED",
        "sender": {"name": "Test Sender"},
        "receivingTimeIndication": {"indicationType": "MomentIndication", "moment": "2026-05-30T14:00:00Z"},
    }


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
    parcel = _delivered_parcel()
    parcel["sender"] = None
    sensor = DhlDeliveredParcelsSensor(_make_coordinator([], [parcel]), USER_INFO)
    attrs = sensor.extra_state_attributes
    assert attrs["parcels"][0]["sender"] is None
