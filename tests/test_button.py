"""Tests for the DHL refresh button."""
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from custom_components.dhl_nl.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
)

_ENTRY_DATA = {CONF_EMAIL: "user@example.com", CONF_PASSWORD: "secret"}
_USER_INFO = {"userId": "abc123", "email": _ENTRY_DATA[CONF_EMAIL]}


def _add_entry(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_ENTRY_DATA[CONF_EMAIL],
        data=_ENTRY_DATA,
        options={
            CONF_DELIVERED_FILTER_TYPE: "days",
            CONF_DELIVERED_FILTER_AMOUNT: 7,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.asyncio
async def test_refresh_button_forces_a_poll(hass):
    """Pressing the refresh button re-polls both coordinators."""
    entry = _add_entry(hass)
    with (
        patch(
            "custom_components.dhl_nl.DhlApiClient.async_login",
            new=AsyncMock(return_value=_USER_INFO),
        ),
        patch(
            "custom_components.dhl_nl.DhlApiClient.async_get_parcels",
            new=AsyncMock(return_value=[]),
        ) as mock_get_parcels,
        patch(
            "custom_components.dhl_nl.DhlApiClient.async_get_sent_shipments",
            new=AsyncMock(return_value=[]),
        ) as mock_get_sent,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "button", DOMAIN, f"{_USER_INFO['userId']}_refresh"
        )
        assert entity_id is not None
        assert hass.states.get(entity_id) is not None

        parcels_before = mock_get_parcels.await_count
        sent_before = mock_get_sent.await_count

        await hass.services.async_call(
            "button", "press", {"entity_id": entity_id}, blocking=True
        )
        await hass.async_block_till_done()

    assert mock_get_parcels.await_count > parcels_before
    assert mock_get_sent.await_count > sent_before
