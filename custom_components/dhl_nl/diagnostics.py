"""Diagnostics support for the DHL Package Tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import DhlConfigEntry

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    "email",
    "userId",
    "barcode",
    "name",
    "receiver",
    "postalCode",
    "street",
    "houseNumber",
    "city",
    "phoneNumber",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DhlConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a DHL config entry."""
    data = entry.runtime_data

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": dict(entry.options),
        "user_info": async_redact_data(data.user_info, TO_REDACT),
        "counts": {
            "incoming_active": len(data.coordinator.data or []),
            "delivered": len(data.coordinator.delivered or []),
            "outgoing_active": len(data.sent_coordinator.data or []),
        },
        "incoming": async_redact_data(data.coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(data.coordinator.delivered or [], TO_REDACT),
        "outgoing": async_redact_data(data.sent_coordinator.data or [], TO_REDACT),
    }
