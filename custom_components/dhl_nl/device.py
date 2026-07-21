"""The device every entity of this integration belongs to.

One place, because sensors, the button and the calendar must all land on the
*same* device entry. It used to be defined three times — once per platform —
with the button and calendar docstrings noting that they mirrored the sensor's
copy, which is exactly the kind of duplication that drifts.
"""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN


def build_device_info(user_info: dict[str, Any]) -> DeviceInfo:
    """Return a DeviceInfo dict shared by all sensors for this account.

    Device name is ``"DHL (<email>)"`` so the auto-prefixed entity
    friendly names read as ``"DHL (account@example.com) Incoming
    parcels"``. Including the account in the device name disambiguates
    users with multiple DHL accounts and matches mainstream HA style for
    cloud-account integrations.
    """
    user_id: str = user_info.get("userId", "")
    email: str = user_info.get("email", "")
    device_name = f"DHL ({email})" if email else "DHL"
    return DeviceInfo(
        identifiers={(DOMAIN, user_id)},
        name=device_name,
        manufacturer="DHL",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://my.dhlecommerce.nl",
    )
