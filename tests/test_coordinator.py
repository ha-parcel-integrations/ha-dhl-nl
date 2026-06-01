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
)


def _mock_entry(filter_type: str = "days", filter_amount: int = 7) -> MagicMock:
    entry = MagicMock()
    entry.options = {
        CONF_DELIVERED_FILTER_TYPE: filter_type,
        CONF_DELIVERED_FILTER_AMOUNT: filter_amount,
    }
    return entry


def _parcel(category: str, is_return: bool = False, moment: str | None = None) -> dict:
    indication = (
        {"indicationType": "MomentIndication", "moment": moment} if moment else None
    )
    return {
        "barcode": "TEST123",
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
    assert result[0]["category"] == "IN_DELIVERY"


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
    assert coordinator.delivered[0]["category"] == "DELIVERED"
