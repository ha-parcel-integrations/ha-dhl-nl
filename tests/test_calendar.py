"""Tests for the DHL deliveries calendar."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.dhl_nl.calendar import DhlDeliveriesCalendar
from custom_components.dhl_nl.coordinator import normalize_parcel

USER_INFO = {"userId": "user123", "email": "test@example.com"}


def _make_coordinator(parcels: list[dict]) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = parcels
    return coordinator


def _parcel(
    barcode: str,
    indication: dict | None = None,
    location_type: str = "ADDRESS",
) -> dict:
    return normalize_parcel({
        "barcode": barcode,
        "status": "OUT_FOR_DELIVERY",
        "category": "IN_DELIVERY",
        "destination": {"locationType": location_type, "name": "DHL ServicePoint"},
        "sender": {"name": "Example Sender"},
        "receivingTimeIndication": indication,
    })


def _moment(moment: str) -> dict:
    return {"indicationType": "MomentIndication", "moment": moment}


def _interval(start: str, end: str) -> dict:
    return {"indicationType": "IntervalIndication", "start": start, "end": end}


def _calendar(parcels: list[dict]) -> DhlDeliveriesCalendar:
    return DhlDeliveriesCalendar(_make_coordinator(parcels), USER_INFO)


def test_event_returns_earliest_upcoming():
    """The entity's ``event`` is the soonest future delivery."""
    cal = _calendar([
        _parcel("LATE", _moment("2099-01-02T10:00:00Z")),
        _parcel("SOON", _moment("2099-01-01T10:00:00Z")),
    ])
    event = cal.event
    assert event is not None
    assert event.uid == "SOON"
    assert event.summary == "Example Sender"


def test_event_none_when_no_planned_parcels():
    """Parcels without a delivery moment produce no events."""
    cal = _calendar([_parcel("NOPLAN", indication=None)])
    assert cal.event is None


def test_moment_indication_gets_one_hour_duration():
    """A single moment (no interval end) yields a one-hour event."""
    cal = _calendar([_parcel("A", _moment("2099-01-01T10:00:00Z"))])
    events = cal._events()
    assert len(events) == 1
    start = datetime(2099, 1, 1, 10, 0, tzinfo=timezone.utc)
    assert events[0].start == start
    assert events[0].end == datetime(2099, 1, 1, 11, 0, tzinfo=timezone.utc)


def test_interval_indication_uses_window():
    """An interval indication maps straight onto the event window."""
    cal = _calendar([
        _parcel("A", _interval("2099-01-01T10:00:00Z", "2099-01-01T12:00:00Z"))
    ])
    events = cal._events()
    assert events[0].end == datetime(2099, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_pickup_parcel_sets_location():
    """A ServicePoint-destined parcel exposes the pickup point as location."""
    cal = _calendar([
        _parcel("A", _moment("2099-01-01T10:00:00Z"), location_type="SERVICEPOINT")
    ])
    assert cal._events()[0].location == "DHL ServicePoint"


async def test_get_events_filters_by_range():
    """async_get_events only returns events overlapping the requested window."""
    cal = _calendar([
        _parcel("PAST", _moment("2000-01-01T10:00:00Z")),
        _parcel("FUTURE", _moment("2099-01-01T10:00:00Z")),
    ])
    start = datetime(2098, 1, 1, tzinfo=timezone.utc)
    end = datetime(2100, 1, 1, tzinfo=timezone.utc)
    events = await cal.async_get_events(MagicMock(), start, end)
    assert {e.uid for e in events} == {"FUTURE"}
