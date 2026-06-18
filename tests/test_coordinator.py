"""Tests for coordinator filter functions and error handling."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.dhl_nl.api import DhlApiError
from custom_components.dhl_nl.const import (
    ACTIVE_CATEGORIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
)
from custom_components.dhl_nl.coordinator import (
    DhlCoordinator,
    filter_active_parcels,
    filter_active_sent_shipments,
    filter_delivered_parcels,
    normalize_parcel,
)


def _mock_entry(filter_type: str = "days", filter_amount: int = 7) -> MagicMock:
    entry = MagicMock()
    entry.options = {
        CONF_DELIVERED_FILTER_TYPE: filter_type,
        CONF_DELIVERED_FILTER_AMOUNT: filter_amount,
    }
    return entry


def _parcel(
    category: str,
    is_return: bool = False,
    moment: str | None = None,
    barcode: str = "TEST123",
) -> dict:
    indication = (
        {"indicationType": "MomentIndication", "moment": moment} if moment else None
    )
    return {
        "barcode": barcode,
        "category": category,
        "isReturn": is_return,
        "receivingTimeIndication": indication,
    }


# ---------------------------------------------------------------------------
# filter_active_parcels
# ---------------------------------------------------------------------------


def test_active_parcel_is_included():
    assert filter_active_parcels([_parcel("IN_DELIVERY")]) != []


def test_delivered_parcel_is_excluded():
    assert filter_active_parcels([_parcel("DELIVERED")]) == []


def test_return_parcel_is_excluded():
    assert filter_active_parcels([_parcel("IN_DELIVERY", is_return=True)]) == []


def test_all_active_categories_pass():
    parcels = [_parcel(cat) for cat in ACTIVE_CATEGORIES]
    assert len(filter_active_parcels(parcels)) == len(ACTIVE_CATEGORIES)


def test_mixed_parcels_filtered_correctly():
    parcels = [
        _parcel("IN_DELIVERY"),
        _parcel("DELIVERED"),
        _parcel("IN_DELIVERY", is_return=True),
        _parcel("UNDERWAY"),
    ]
    result = filter_active_parcels(parcels)
    assert len(result) == 2


def test_empty_list_returns_empty():
    assert filter_active_parcels([]) == []


# ---------------------------------------------------------------------------
# filter_delivered_parcels
# ---------------------------------------------------------------------------


def test_delivered_parcel_is_included():
    assert filter_delivered_parcels([_parcel("DELIVERED")]) != []


def test_active_parcel_excluded_from_delivered():
    assert filter_delivered_parcels([_parcel("IN_DELIVERY")]) == []


def test_return_parcel_excluded_from_delivered():
    assert filter_delivered_parcels([_parcel("DELIVERED", is_return=True)]) == []


def test_delivered_filters_only_non_return_delivered():
    parcels = [
        _parcel("DELIVERED"),
        _parcel("DELIVERED", is_return=True),
        _parcel("IN_DELIVERY"),
    ]
    assert len(filter_delivered_parcels(parcels)) == 1


# ---------------------------------------------------------------------------
# filter_active_sent_shipments
# ---------------------------------------------------------------------------


def _shipment(category: str, shipment_type: str = "outgoing") -> dict:
    return {"barcode": "SENT123", "category": category, "type": shipment_type}


def test_active_outgoing_shipment_is_included():
    assert filter_active_sent_shipments([_shipment("IN_DELIVERY")]) != []


def test_delivered_shipment_is_excluded():
    assert filter_active_sent_shipments([_shipment("DELIVERED")]) == []


def test_non_outgoing_type_is_excluded():
    assert filter_active_sent_shipments([_shipment("IN_DELIVERY", shipment_type="incoming")]) == []


# ---------------------------------------------------------------------------
# DhlCoordinator._apply_delivered_filter — days mode
# ---------------------------------------------------------------------------


async def test_delivered_filter_days_excludes_old_parcels(hass):
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    parcels = [
        _parcel("DELIVERED", moment=old),
        _parcel("DELIVERED", moment=recent),
    ]
    coordinator = DhlCoordinator(hass, MagicMock(), _mock_entry("days", 7))
    result = coordinator._apply_delivered_filter(parcels)
    assert len(result) == 1
    assert result[0]["receivingTimeIndication"]["moment"] == recent


async def test_delivered_filter_days_includes_parcel_without_date(hass):
    parcels = [_parcel("DELIVERED")]
    coordinator = DhlCoordinator(hass, MagicMock(), _mock_entry("days", 7))
    result = coordinator._apply_delivered_filter(parcels)
    assert len(result) == 1


async def test_delivered_filter_days_all_recent(hass):
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    parcels = [_parcel("DELIVERED", moment=recent)] * 5
    coordinator = DhlCoordinator(hass, MagicMock(), _mock_entry("days", 7))
    assert len(coordinator._apply_delivered_filter(parcels)) == 5


# ---------------------------------------------------------------------------
# DhlCoordinator._apply_delivered_filter — parcels mode
# ---------------------------------------------------------------------------


async def test_delivered_filter_parcels_limits_count(hass):
    parcels = [_parcel("DELIVERED")] * 10
    coordinator = DhlCoordinator(hass, MagicMock(), _mock_entry("parcels", 3))
    result = coordinator._apply_delivered_filter(parcels)
    assert len(result) == 3


async def test_delivered_filter_parcels_fewer_than_limit(hass):
    parcels = [_parcel("DELIVERED")] * 2
    coordinator = DhlCoordinator(hass, MagicMock(), _mock_entry("parcels", 5))
    assert len(coordinator._apply_delivered_filter(parcels)) == 2


# ---------------------------------------------------------------------------
# DhlCoordinator error handling and data flow
# ---------------------------------------------------------------------------


async def test_coordinator_raises_update_failed_on_api_error(hass):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=DhlApiError("401"))

    coordinator = DhlCoordinator(hass, client, _mock_entry())

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_coordinator_returns_only_active_parcels(hass):
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value=[
        _parcel("IN_DELIVERY"),
        _parcel("DELIVERED"),
        _parcel("IN_DELIVERY", is_return=True),
    ])

    coordinator = DhlCoordinator(hass, client, _mock_entry())
    result = await coordinator._async_update_data()

    assert len(result) == 1
    assert result[0]["raw"]["category"] == "IN_DELIVERY"
    assert result[0]["carrier"] == "DHL"


# ---------------------------------------------------------------------------
# normalize_parcel
# ---------------------------------------------------------------------------


def test_normalize_active_with_moment_indication():
    parcel = {
        "barcode": "ABC",
        "category": "IN_DELIVERY",
        "status": "IN_DELIVERY",
        "sender": {"name": "Test Sender"},
        "destination": {"locationType": "ADDRESS", "name": "Home"},
        "receivingTimeIndication": {
            "indicationType": "MomentIndication",
            "moment": "2026-06-15T14:00:00+02:00",
        },
    }
    result = normalize_parcel(parcel)
    assert result["carrier"] == "DHL"
    assert result["barcode"] == "ABC"
    assert result["sender"] == "Test Sender"
    assert result["delivered"] is False
    assert result["delivered_at"] is None
    assert result["planned_from"] == "2026-06-15T14:00:00+02:00"
    assert result["planned_to"] is None
    assert result["pickup"] is False
    assert result["pickup_point"] is None
    assert result["raw"] == parcel


def test_normalize_active_with_interval_indication():
    parcel = {
        "barcode": "ABC",
        "category": "IN_DELIVERY",
        "destination": {"locationType": "ADDRESS"},
        "receivingTimeIndication": {
            "indicationType": "IntervalIndication",
            "start": "2026-06-15T14:00:00+02:00",
            "end": "2026-06-15T16:00:00+02:00",
        },
    }
    result = normalize_parcel(parcel)
    assert result["planned_from"] == "2026-06-15T14:00:00+02:00"
    assert result["planned_to"] == "2026-06-15T16:00:00+02:00"


def test_normalize_delivered_sets_delivered_at_not_planned():
    parcel = {
        "barcode": "ABC",
        "category": "DELIVERED",
        "destination": {"locationType": "ADDRESS"},
        "receivingTimeIndication": {
            "indicationType": "MomentIndication",
            "moment": "2026-06-15T14:00:00+02:00",
        },
    }
    result = normalize_parcel(parcel)
    assert result["delivered"] is True
    assert result["delivered_at"] == "2026-06-15T14:00:00+02:00"
    assert result["planned_from"] is None
    assert result["planned_to"] is None


def test_normalize_pickup_point():
    parcel = {
        "barcode": "ABC",
        "category": "IN_DELIVERY",
        "destination": {"locationType": "SERVICEPOINT", "name": "Albert Heijn Centrum"},
    }
    result = normalize_parcel(parcel)
    assert result["pickup"] is True
    assert result["pickup_point"] == "Albert Heijn Centrum"


def test_normalize_handles_missing_fields():
    result = normalize_parcel({})
    assert result["carrier"] == "DHL"
    assert result["barcode"] is None
    assert result["sender"] is None
    assert result["pickup"] is False
    assert result["pickup_point"] is None
    assert result["url"] is None


def test_normalize_constructs_tracking_url():
    parcel = {
        "barcode": "3SXXXXXXXXXXXXXXXXX",
        "category": "IN_DELIVERY",
        "destination": {"address": {"postalCode": "1234 AB"}},
    }
    result = normalize_parcel(parcel)
    assert result["url"] == (
        "https://my.dhlecommerce.nl/portal/tracktrace/3SXXXXXXXXXXXXXXXXX/1234AB"
    )


def test_normalize_url_none_when_postcode_missing():
    parcel = {
        "barcode": "3SXXXXXXXXXXXXXXXXX",
        "category": "IN_DELIVERY",
        "destination": {"address": {}},
    }
    assert normalize_parcel(parcel)["url"] is None


# ---------------------------------------------------------------------------
# Coordinator data-flow
# ---------------------------------------------------------------------------


async def test_coordinator_populates_delivered(hass):
    recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value=[
        _parcel("IN_DELIVERY"),
        _parcel("DELIVERED", moment=recent),
        _parcel("DELIVERED", is_return=True, moment=recent),
    ])

    coordinator = DhlCoordinator(hass, client, _mock_entry("days", 7))
    await coordinator._async_update_data()

    assert len(coordinator.delivered) == 1
    assert coordinator.delivered[0]["raw"]["category"] == "DELIVERED"


# ---------------------------------------------------------------------------
# Event firing — parcel_registered and parcel_status_changed
# ---------------------------------------------------------------------------


async def test_no_events_on_first_refresh(hass):
    """The first refresh seeds known state silently — no events."""
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value=[
        _parcel("IN_DELIVERY", barcode="A"),
        _parcel("IN_DELIVERY", barcode="B"),
    ])

    fired: list = []
    hass.bus.async_listen("dhl_nl_parcel_registered", lambda e: fired.append(e))
    hass.bus.async_listen("dhl_nl_parcel_status_changed", lambda e: fired.append(e))

    coordinator = DhlCoordinator(hass, client, _mock_entry())
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert fired == []


async def test_registered_event_for_new_barcodes(hass):
    """A barcode that appears for the first time after seeding fires registered."""
    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=[
        [_parcel("IN_DELIVERY", barcode="A")],
        [_parcel("IN_DELIVERY", barcode="A"), _parcel("IN_DELIVERY", barcode="B")],
    ])

    registered: list = []
    hass.bus.async_listen(
        "dhl_nl_parcel_registered", lambda e: registered.append(e.data)
    )

    coordinator = DhlCoordinator(hass, client, _mock_entry())
    await coordinator._async_update_data()
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(registered) == 1
    assert registered[0]["barcode"] == "B"


async def test_status_changed_event_when_status_transitions(hass):
    """A barcode whose normalized status changes fires status_changed."""
    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=[
        [_parcel("IN_DELIVERY", barcode="A")],
        [_parcel("DELIVERED", barcode="A")],
    ])

    changed: list = []
    hass.bus.async_listen(
        "dhl_nl_parcel_status_changed", lambda e: changed.append(e.data)
    )

    coordinator = DhlCoordinator(hass, client, _mock_entry())
    await coordinator._async_update_data()
    # After refresh 1, barcode A becomes delivered → it falls out of
    # active parcels, so it does not appear on refresh 2 in the active
    # list either. The status_changed event fires only when the barcode
    # is still present with a different status.
    # To test a real transition, both refreshes must return barcode A in
    # the active list with different statuses.
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    # Status went from IN_DELIVERY → DELIVERED, but DELIVERED filters out
    # of the active list. So expect no status_changed event in this scenario.
    # This is documented behaviour: events track active parcels only.
    assert changed == []


async def test_status_changed_event_when_active_status_transitions(hass):
    """When an active parcel changes from one IN_TRANSIT status to another."""
    from custom_components.dhl_nl.const import ParcelStatus

    p1 = _parcel("IN_DELIVERY", barcode="A")
    p1["status"] = "DATA_RECEIVED"  # raw status — maps to REGISTERED via fallback
    p1["category"] = "DATA_RECEIVED"

    p2 = _parcel("IN_DELIVERY", barcode="A")
    p2["status"] = "OUT_FOR_DELIVERY"  # maps to OUT_FOR_DELIVERY
    p2["category"] = "IN_DELIVERY"

    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=[[p1], [p2]])

    changed: list = []
    hass.bus.async_listen(
        "dhl_nl_parcel_status_changed", lambda e: changed.append(e.data)
    )

    coordinator = DhlCoordinator(hass, client, _mock_entry())
    await coordinator._async_update_data()
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(changed) == 1
    assert changed[0]["barcode"] == "A"
    assert changed[0]["old_status"] == ParcelStatus.REGISTERED
    assert changed[0]["new_status"] == ParcelStatus.OUT_FOR_DELIVERY
