"""Tests for coordinator filter functions and error handling."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.dhl_nl.api import DhlApiError
from custom_components.dhl_nl.coordinator import (
    DhlCoordinator,
    filter_active_parcels,
    filter_active_sent_shipments,
)

# ---------------------------------------------------------------------------
# filter_active_parcels
# ---------------------------------------------------------------------------

def _parcel(category: str, is_return: bool = False) -> dict:
    return {"barcode": "TEST123", "category": category, "isReturn": is_return}


def test_active_parcel_is_included():
    assert filter_active_parcels([_parcel("IN_DELIVERY")]) != []


def test_delivered_parcel_is_excluded():
    assert filter_active_parcels([_parcel("DELIVERED")]) == []


def test_return_parcel_is_excluded():
    assert filter_active_parcels([_parcel("IN_DELIVERY", is_return=True)]) == []


def test_all_active_categories_pass():
    from custom_components.dhl_nl.const import ACTIVE_CATEGORIES
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
# DhlCoordinator error handling
# ---------------------------------------------------------------------------

async def test_coordinator_raises_update_failed_on_api_error(hass):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=DhlApiError("401"))

    coordinator = DhlCoordinator(hass, client)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_coordinator_returns_only_active_parcels(hass):
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value=[
        _parcel("IN_DELIVERY"),
        _parcel("DELIVERED"),
        _parcel("IN_DELIVERY", is_return=True),
    ])

    coordinator = DhlCoordinator(hass, client)
    result = await coordinator._async_update_data()

    assert len(result) == 1
    assert result[0]["category"] == "IN_DELIVERY"
