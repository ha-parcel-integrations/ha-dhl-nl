"""Tests for the DHL diagnostics handler."""
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.dhl_nl import DhlData
from custom_components.dhl_nl.diagnostics import (
    TO_REDACT,
    async_get_config_entry_diagnostics,
)

REDACTED = "**REDACTED**"


def _entry_with_runtime_data(
    *,
    incoming: list[dict] | None = None,
    delivered: list[dict] | None = None,
    returning: list[dict] | None = None,
    delivered_outgoing: list[dict] | None = None,
    outgoing: list[dict] | None = None,
    outgoing_delivered: list[dict] | None = None,
    user_info: dict | None = None,
    current_tier_minutes: int | None = 45,
    update_interval: timedelta | None = timedelta(minutes=45),
    sent_current_tier_minutes: int | None = 45,
    sent_update_interval: timedelta | None = timedelta(minutes=45),
) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = incoming or []
    coordinator.delivered = delivered or []
    coordinator.returning = returning or []
    coordinator.delivered_outgoing = delivered_outgoing or []
    # Explicit, not left as an unconfigured MagicMock attribute, or the
    # "polling" block below would carry a MagicMock instead of a real value.
    coordinator.current_tier_minutes = current_tier_minutes
    coordinator.update_interval = update_interval
    sent_coordinator = MagicMock()
    sent_coordinator.data = outgoing or []
    sent_coordinator.delivered = outgoing_delivered or []
    sent_coordinator.current_tier_minutes = sent_current_tier_minutes
    sent_coordinator.update_interval = sent_update_interval

    entry = MagicMock()
    entry.data = {"email": "user@example.com", "password": "secret"}
    entry.options = {"delivered_filter_type": "days", "delivered_filter_amount": 7}
    entry.runtime_data = DhlData(
        client=MagicMock(),
        coordinator=coordinator,
        sent_coordinator=sent_coordinator,
        user_info=user_info or {"email": "user@example.com", "userId": "abc123"},
        session=MagicMock(),
    )
    return entry


@pytest.mark.asyncio
async def test_diagnostics_redacts_credentials_and_user_info():
    entry = _entry_with_runtime_data()
    result = await async_get_config_entry_diagnostics(MagicMock(), entry)

    assert result["entry_data"]["email"] == REDACTED
    assert result["entry_data"]["password"] == REDACTED
    assert result["user_info"]["email"] == REDACTED
    assert result["user_info"]["userId"] == REDACTED


@pytest.mark.asyncio
async def test_diagnostics_passes_through_options():
    entry = _entry_with_runtime_data()
    result = await async_get_config_entry_diagnostics(MagicMock(), entry)
    assert result["entry_options"]["delivered_filter_type"] == "days"
    assert result["entry_options"]["delivered_filter_amount"] == 7


@pytest.mark.asyncio
async def test_diagnostics_redacts_parcel_barcode_and_address():
    entry = _entry_with_runtime_data(
        incoming=[{
            "barcode": "3SABC123",
            "receiver": "Jane Doe",
            "sender": {"name": "Brand"},
            "destination": {
                "address": {
                    "postalCode": "1234AB",
                    "street": "Hoofdstraat",
                    "houseNumber": "42",
                    "city": "Amsterdam",
                }
            },
        }],
    )
    result = await async_get_config_entry_diagnostics(MagicMock(), entry)
    parcel = result["incoming"][0]
    assert parcel["barcode"] == REDACTED
    assert parcel["destination"]["address"]["postalCode"] == REDACTED
    assert parcel["destination"]["address"]["street"] == REDACTED
    assert parcel["destination"]["address"]["houseNumber"] == REDACTED
    assert parcel["destination"]["address"]["city"] == REDACTED
    # Person/shop names are PII too — redacted since the 2.4.x polish.
    assert parcel["receiver"] == REDACTED
    assert parcel["sender"]["name"] == REDACTED


@pytest.mark.asyncio
async def test_diagnostics_reports_counts():
    entry = _entry_with_runtime_data(
        incoming=[{"barcode": "A"}, {"barcode": "B"}],
        delivered=[{"barcode": "C"}],
        returning=[{"barcode": "R1"}],
        delivered_outgoing=[{"barcode": "R2"}, {"barcode": "R3"}],
        outgoing=[{"barcode": "D"}, {"barcode": "E"}, {"barcode": "F"}],
    )
    result = await async_get_config_entry_diagnostics(MagicMock(), entry)
    assert result["counts"] == {
        "incoming_active": 2,
        "delivered": 1,
        "returning": 1,
        "delivered_outgoing": 2,
        "outgoing_active": 3,
        "outgoing_delivered": 0,
    }
    assert [p["barcode"] for p in result["returning"]] == ["**REDACTED**"]


@pytest.mark.asyncio
async def test_diagnostics_surfaces_polling_state():
    entry = _entry_with_runtime_data(
        current_tier_minutes=15,
        update_interval=timedelta(minutes=15),
        sent_current_tier_minutes=45,
        sent_update_interval=timedelta(minutes=45),
    )
    result = await async_get_config_entry_diagnostics(MagicMock(), entry)
    assert result["polling"] == {
        "current_tier_minutes": 15,
        "update_interval_seconds": 15 * 60,
        "sent_current_tier_minutes": 45,
        "sent_update_interval_seconds": 45 * 60,
    }


@pytest.mark.asyncio
async def test_diagnostics_polling_handles_fixed_interval_mode():
    entry = _entry_with_runtime_data(
        current_tier_minutes=None,
        update_interval=timedelta(minutes=30),
        sent_current_tier_minutes=None,
        sent_update_interval=timedelta(minutes=30),
    )
    result = await async_get_config_entry_diagnostics(MagicMock(), entry)
    assert result["polling"] == {
        "current_tier_minutes": None,
        "update_interval_seconds": 30 * 60,
        "sent_current_tier_minutes": None,
        "sent_update_interval_seconds": 30 * 60,
    }


def test_to_redact_includes_pii_keys():
    for key in ("email", "password", "userId", "barcode", "postalCode"):
        assert key in TO_REDACT
