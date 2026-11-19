"""Tests for the DHL deliveries calendar."""

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

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
    return normalize_parcel(
        {
            "barcode": barcode,
            "status": "OUT_FOR_DELIVERY",
            "category": "IN_DELIVERY",
            "destination": {"locationType": location_type, "name": "DHL ServicePoint"},
            "sender": {"name": "Example Sender"},
            "receivingTimeIndication": indication,
        }
    )


def _moment(moment: str) -> dict:
    return {"indicationType": "MomentIndication", "moment": moment}


def _interval(start: str, end: str) -> dict:
    return {"indicationType": "IntervalIndication", "start": start, "end": end}


def _calendar(parcels: list[dict]) -> DhlDeliveriesCalendar:
    return DhlDeliveriesCalendar(_make_coordinator(parcels), USER_INFO)


def test_event_returns_earliest_upcoming():
    """The entity's ``event`` is the soonest future delivery."""
    cal = _calendar(
        [
            _parcel("LATE", _moment("2099-01-02T10:00:00Z")),
            _parcel("SOON", _moment("2099-01-01T10:00:00Z")),
        ]
    )
    event = cal.event
    assert event is not None
    assert event.uid == "SOON"
    assert event.summary == "Example Sender"


def test_event_none_when_no_planned_parcels():
    """Parcels without a delivery moment produce no events."""
    cal = _calendar([_parcel("NOPLAN", indication=None)])
    assert cal.event is None


def test_moment_indication_gets_one_hour_duration():
    """A parcel without a window end falls back to a one-hour event."""
    cal = _calendar([_planned("A", "2099-01-01T10:00:00Z", None)])
    events = cal._events()
    assert len(events) == 1
    start = datetime(2099, 1, 1, 10, 0, tzinfo=UTC)
    assert events[0].start == start
    assert events[0].end == datetime(2099, 1, 1, 11, 0, tzinfo=UTC)


def test_moment_indication_runs_to_the_end_of_the_day():
    """A single moment is stretched to the end of its day."""
    cal = _calendar([_parcel("A", _moment("2099-01-01T10:00:00+01:00"))])
    event = cal._events()[0]
    assert event.start == datetime.fromisoformat("2099-01-01T10:00:00+01:00")
    assert event.end == datetime.fromisoformat("2099-01-01T23:59:59+01:00")


def test_interval_indication_uses_window():
    """An interval indication maps straight onto the event window."""
    cal = _calendar(
        [_parcel("A", _interval("2099-01-01T10:00:00Z", "2099-01-01T12:00:00Z"))]
    )
    events = cal._events()
    assert events[0].end == datetime(2099, 1, 1, 12, 0, tzinfo=UTC)


def test_pickup_parcel_sets_location():
    """A ServicePoint-destined parcel exposes the pickup point as location."""
    cal = _calendar(
        [_parcel("A", _moment("2099-01-01T10:00:00Z"), location_type="SERVICEPOINT")]
    )
    assert cal._events()[0].location == "DHL ServicePoint"


async def test_get_events_filters_by_range():
    """async_get_events only returns events overlapping the requested window."""
    cal = _calendar(
        [
            _parcel("PAST", _moment("2000-01-01T10:00:00Z")),
            _parcel("FUTURE", _moment("2099-01-01T10:00:00Z")),
        ]
    )
    start = datetime(2098, 1, 1, tzinfo=UTC)
    end = datetime(2100, 1, 1, tzinfo=UTC)
    events = await cal.async_get_events(MagicMock(), start, end)
    assert {e.uid for e in events} == {"FUTURE"}


def _planned(barcode: str, planned_from: str, planned_to: str | None) -> dict:
    return {
        "barcode": barcode,
        "status": "out_for_delivery",
        "sender": "Example Sender",
        "pickup": False,
        "planned_from": planned_from,
        "planned_to": planned_to,
    }


def test_whole_day_window_is_an_all_day_event():
    cal = _calendar(
        [_planned("A", "2099-01-01T00:00:00+01:00", "2099-01-01T23:59:59+01:00")]
    )
    event = cal._events()[0]
    assert (event.start, event.end) == (date(2099, 1, 1), date(2099, 1, 2))
    assert event.all_day


def test_window_over_whole_days_is_one_all_day_event():
    cal = _calendar([_planned("A", "2099-01-01T00:00:00", "2099-01-03T23:59:59")])
    event = cal._events()[0]
    assert (event.start, event.end) == (date(2099, 1, 1), date(2099, 1, 4))


async def test_whole_day_window_sent_in_utc_is_all_day_in_local_time(hass):
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    cal = _calendar([_planned("A", "2099-01-01T23:00:00Z", "2099-01-02T22:59:59Z")])
    event = cal._events()[0]
    assert (event.start, event.end) == (date(2099, 1, 2), date(2099, 1, 3))


def test_a_moment_stretched_to_the_end_of_its_day_stays_timed():
    cal = _calendar(
        [_planned("A", "2099-01-01T14:00:00+01:00", "2099-01-01T23:59:59+01:00")]
    )
    assert not cal._events()[0].all_day


async def test_all_day_events_are_upcoming_and_filtered_by_range():
    cal = _calendar(
        [
            _planned("PAST", "2000-01-01T00:00:00", "2000-01-01T23:59:59"),
            _planned("FUTURE", "2099-01-01T00:00:00", "2099-01-01T23:59:59"),
        ]
    )
    assert cal.event.uid == "FUTURE"
    start = datetime(2098, 1, 1, tzinfo=UTC)
    end = datetime(2100, 1, 1, tzinfo=UTC)
    events = await cal.async_get_events(MagicMock(), start, end)
    assert {e.uid for e in events} == {"FUTURE"}
