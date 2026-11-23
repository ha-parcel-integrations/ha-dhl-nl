"""Tests for the contents action, the overlay and the store lifecycle."""

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.dhl_nl.const import DOMAIN
from custom_components.dhl_nl.coordinator import (
    DhlCoordinator,
    DhlSentShipmentsCoordinator,
)
from custom_components.dhl_nl.parcels import normalize_parcel

from .payloads import (
    ACTIVE_BARCODE,
    DELIVERED_BARCODE,
    SENT_BARCODE,
    delivered_sample,
    detail_sample,
    shipment_sample,
)

CLIENT = "custom_components.dhl_nl.DhlApiClient"
REFRESH = f"{DhlCoordinator.__module__}.DhlCoordinator"
SENT_REFRESH = f"{DhlSentShipmentsCoordinator.__module__}.DhlSentShipmentsCoordinator"
UNTRACKED = "ZZ555555"
OTHER = "OTHER777"
STORE_KEY = f"{DOMAIN}.contents.%s"


def _sent() -> dict:
    return shipment_sample("IN_DELIVERY")


def _recent_delivered() -> dict:
    parcel = delivered_sample()
    parcel["receivingTimeIndication"]["moment"] = dt_util.utcnow().isoformat()
    return parcel


@contextmanager
def _carrier(parcels: list[dict]):
    with (
        patch(f"{CLIENT}.async_get_parcels", new=AsyncMock(return_value=parcels)),
        patch(f"{CLIENT}.async_get_sent_shipments", new=AsyncMock(return_value=[])),
    ):
        yield


def _config_entry(email: str = "user@example.test") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=email,
        unique_id=email,
        data={CONF_EMAIL: email, CONF_PASSWORD: "hunter2"},
    )


async def _setup(
    hass,
    entry: MockConfigEntry | None = None,
    parcels: list[dict] | None = None,
    sent: list[dict] | None = None,
) -> MockConfigEntry:
    entry = entry or _config_entry()
    parcels = [detail_sample()] if parcels is None else parcels
    entry.add_to_hass(hass)
    with (
        patch(
            f"{CLIENT}.async_login",
            new=AsyncMock(return_value={"userId": entry.unique_id}),
        ),
        patch(f"{CLIENT}.async_get_parcels", new=AsyncMock(return_value=parcels)),
        patch(
            f"{CLIENT}.async_get_sent_shipments",
            new=AsyncMock(return_value=sent or []),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _call(hass, data: dict) -> None:
    with (
        patch(
            f"{CLIENT}.async_get_parcels",
            new=AsyncMock(return_value=[detail_sample()]),
        ),
        patch(
            f"{CLIENT}.async_get_sent_shipments",
            new=AsyncMock(return_value=[_sent()]),
        ),
    ):
        await hass.services.async_call(DOMAIN, "update_contents", data, blocking=True)
        await hass.async_block_till_done()
        # A refresh requested inside the debouncer's cooldown runs after it.
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
        await hass.async_block_till_done()


def _contents(entry: MockConfigEntry, code: str) -> str | None:
    parcels = {p["barcode"]: p for p in entry.runtime_data.coordinator.data}
    return parcels[code]["contents"]


def _stored(entry: MockConfigEntry, code: str) -> str | None:
    return entry.runtime_data.coordinator.contents_store.get(code)


async def test_a_parcel_has_no_contents_without_a_stored_value(hass):
    entry = await _setup(hass)
    assert _contents(entry, ACTIVE_BARCODE) is None


async def test_update_contents_sets_replaces_and_clears(hass):
    entry = await _setup(hass)

    await _call(hass, {"barcode": ACTIVE_BARCODE, "contents": " Running  shoes "})
    assert _contents(entry, ACTIVE_BARCODE) == "Running shoes"

    await _call(hass, {"barcode": ACTIVE_BARCODE, "contents": "Winter socks"})
    assert _contents(entry, ACTIVE_BARCODE) == "Winter socks"

    await _call(hass, {"barcode": ACTIVE_BARCODE, "contents": "  "})
    assert _contents(entry, ACTIVE_BARCODE) is None


async def test_update_contents_without_text_clears(hass):
    entry = await _setup(hass)
    await _call(hass, {"barcode": ACTIVE_BARCODE, "contents": "Running shoes"})

    await _call(hass, {"barcode": ACTIVE_BARCODE})

    assert _contents(entry, ACTIVE_BARCODE) is None


async def test_update_contents_normalizes_the_code(hass):
    entry = await _setup(hass)
    await _call(hass, {"barcode": f" {ACTIVE_BARCODE.lower()} ", "contents": "Shoes"})
    assert _contents(entry, ACTIVE_BARCODE) == "Shoes"


async def test_update_contents_works_for_a_code_the_account_does_not_list(hass):
    entry = await _setup(hass)
    await _call(hass, {"barcode": UNTRACKED, "contents": "Running shoes"})

    assert _stored(entry, UNTRACKED) == "Running shoes"


async def test_update_contents_refreshes_only_when_something_changed(hass):
    await _setup(hass)
    data = {"barcode": ACTIVE_BARCODE, "contents": "Running shoes"}
    with (
        patch(f"{REFRESH}.async_request_refresh", new=AsyncMock()) as refresh,
        patch(f"{SENT_REFRESH}.async_request_refresh", new=AsyncMock()) as sent,
    ):
        await _call(hass, data)
        assert (refresh.await_count, sent.await_count) == (1, 1)
        await _call(hass, data)
        assert refresh.await_count == 1
        await _call(hass, {"barcode": UNTRACKED})
        assert refresh.await_count == 1


async def test_update_contents_reaches_sent_shipments(hass):
    entry = await _setup(hass, sent=[_sent()])
    await _call(hass, {"barcode": SENT_BARCODE, "contents": "Return parcel"})

    assert entry.runtime_data.sent_coordinator.data[0]["contents"] == "Return parcel"


async def test_update_contents_rejects_an_empty_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await _call(hass, {"barcode": "  "})


async def test_update_contents_needs_a_loaded_entry(hass):
    entry = await _setup(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    try:
        with pytest.raises(ServiceValidationError):
            await _call(hass, {"barcode": ACTIVE_BARCODE, "contents": "Shoes"})
    finally:
        entry.mock_state(hass, ConfigEntryState.LOADED)


async def test_the_action_goes_with_the_last_loaded_account(hass):
    first = await _setup(hass)
    second = await _setup(hass, _config_entry("other@example.test"))

    assert await hass.config_entries.async_unload(first.entry_id)
    assert hass.services.has_service(DOMAIN, "update_contents")

    assert await hass.config_entries.async_unload(second.entry_id)
    assert not hass.services.has_service(DOMAIN, "update_contents")


async def test_a_stored_value_survives_a_restart_of_the_entry(hass, hass_storage):
    entry = await _setup(hass)
    await _call(hass, {"barcode": ACTIVE_BARCODE, "contents": "Running shoes"})
    store = entry.runtime_data.coordinator.contents_store
    await store._store.async_save(store._data_to_save())

    assert await hass.config_entries.async_unload(entry.entry_id)
    entry = await _setup_again(hass, entry)

    assert _contents(entry, ACTIVE_BARCODE) == "Running shoes"


async def _setup_again(hass, entry: MockConfigEntry) -> MockConfigEntry:
    with (
        patch(f"{CLIENT}.async_login", new=AsyncMock(return_value={"userId": "u"})),
        patch(
            f"{CLIENT}.async_get_parcels",
            new=AsyncMock(return_value=[detail_sample()]),
        ),
        patch(f"{CLIENT}.async_get_sent_shipments", new=AsyncMock(return_value=[])),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_an_entry_without_a_store_file_works(hass, hass_storage):
    entry = await _setup(hass)

    assert STORE_KEY % entry.entry_id not in hass_storage
    assert _contents(entry, ACTIVE_BARCODE) is None


async def test_removing_the_entry_removes_its_store(hass, hass_storage):
    entry = await _setup(hass)
    await _call(hass, {"barcode": ACTIVE_BARCODE, "contents": "Running shoes"})
    store = entry.runtime_data.coordinator.contents_store
    await store._store.async_save(store._data_to_save())
    key = STORE_KEY % entry.entry_id
    assert key in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert key not in hass_storage


async def test_a_contents_change_alone_fires_no_event(hass):
    await _setup(hass)
    events = []
    for name in ("registered", "status_changed", "delivered"):
        hass.bus.async_listen(f"{DOMAIN}_parcel_{name}", events.append)

    await _call(hass, {"barcode": ACTIVE_BARCODE, "contents": "Running shoes"})

    assert events == []


async def test_user_contents_reaches_the_delivered_list(hass):
    entry = await _setup(hass, parcels=[_recent_delivered()])
    entry.runtime_data.coordinator.contents_store.async_set(
        DELIVERED_BARCODE, "Running shoes"
    )
    with _carrier([_recent_delivered()]):
        await entry.runtime_data.coordinator.async_refresh()

    assert entry.runtime_data.coordinator.delivered[0]["contents"] == "Running shoes"


async def test_user_contents_replaces_the_carrier_value_and_clearing_restores_it(hass):
    entry = await _setup(hass)
    coordinator = entry.runtime_data.coordinator

    def with_carrier_text(parcel: dict, **kwargs) -> dict:
        normalized = normalize_parcel(parcel, **kwargs)
        normalized["contents"] = "Carrier text"
        return normalized

    with (
        _carrier([detail_sample()]),
        patch(
            "custom_components.dhl_nl.coordinator.normalize_parcel", with_carrier_text
        ),
    ):
        await coordinator.async_refresh()
        assert _contents(entry, ACTIVE_BARCODE) == "Carrier text"
        coordinator.contents_store.async_set(ACTIVE_BARCODE, "Running shoes")
        await coordinator.async_refresh()
        assert _contents(entry, ACTIVE_BARCODE) == "Running shoes"
        coordinator.contents_store.async_set(ACTIVE_BARCODE, None)
        await coordinator.async_refresh()
        assert _contents(entry, ACTIVE_BARCODE) == "Carrier text"


async def test_a_sent_coordinator_without_a_store_overlays_nothing(hass):
    entry = MagicMock()
    entry.entry_id = "e"
    entry.options = {}
    client = MagicMock()
    client.async_get_sent_shipments = AsyncMock(return_value=[_sent()])
    coordinator = DhlSentShipmentsCoordinator(hass, client, entry)

    data = await coordinator._async_update_data()

    assert data[0]["contents"] is None
    assert coordinator.known_codes == {SENT_BARCODE}


# ---------------------------------------------------------------------------
# several accounts
# ---------------------------------------------------------------------------


async def _two_accounts(hass, first_parcels, second_parcels, second_sent=None):
    first = await _setup(hass, parcels=first_parcels)
    second = await _setup(
        hass, _config_entry("other@example.test"), second_parcels, second_sent
    )
    return first, second


async def _call_quiet(hass, data: dict) -> AsyncMock:
    with (
        patch(f"{REFRESH}.async_request_refresh", new=AsyncMock()) as refresh,
        patch(f"{SENT_REFRESH}.async_request_refresh", new=AsyncMock()),
    ):
        await hass.services.async_call(DOMAIN, "update_contents", data, blocking=True)
        await hass.async_block_till_done()
    return refresh


async def test_several_accounts_store_in_the_one_that_lists_the_code(hass):
    first, second = await _two_accounts(hass, [detail_sample()], [detail_sample(OTHER)])

    refresh = await _call_quiet(hass, {"barcode": OTHER, "contents": "Shoes"})

    assert _stored(first, OTHER) is None
    assert _stored(second, OTHER) == "Shoes"
    assert refresh.await_count == 1


async def test_several_accounts_find_a_delivered_parcel(hass):
    first, second = await _two_accounts(hass, [detail_sample()], [delivered_sample()])

    await _call_quiet(hass, {"barcode": DELIVERED_BARCODE, "contents": "Shoes"})

    assert _stored(first, DELIVERED_BARCODE) is None
    assert _stored(second, DELIVERED_BARCODE) == "Shoes"


async def test_several_accounts_find_a_sent_shipment(hass):
    first, second = await _two_accounts(hass, [detail_sample()], [], [_sent()])

    await _call_quiet(hass, {"barcode": SENT_BARCODE, "contents": "Shoes"})

    assert _stored(first, SENT_BARCODE) is None
    assert _stored(second, SENT_BARCODE) == "Shoes"


async def test_a_code_in_two_accounts_is_stored_in_both(hass):
    first, second = await _two_accounts(hass, [detail_sample()], [detail_sample()])

    refresh = await _call_quiet(hass, {"barcode": ACTIVE_BARCODE, "contents": "Shoes"})

    assert _stored(first, ACTIVE_BARCODE) == "Shoes"
    assert _stored(second, ACTIVE_BARCODE) == "Shoes"
    assert refresh.await_count == 2


async def test_several_accounts_refresh_only_where_the_value_changed(hass):
    first, second = await _two_accounts(hass, [detail_sample()], [detail_sample()])
    first.runtime_data.coordinator.contents_store.async_set(ACTIVE_BARCODE, "Shoes")

    refresh = await _call_quiet(hass, {"barcode": ACTIVE_BARCODE, "contents": "Shoes"})

    assert _stored(second, ACTIVE_BARCODE) == "Shoes"
    assert refresh.await_count == 1


async def test_an_unknown_code_with_several_accounts_needs_an_account(hass):
    first, second = await _two_accounts(hass, [detail_sample()], [detail_sample()])

    with pytest.raises(ServiceValidationError) as raised:
        await _call_quiet(hass, {"barcode": UNTRACKED, "contents": "Shoes"})

    assert raised.value.translation_key == "update_contents_entry_required"
    assert _stored(first, UNTRACKED) is None
    assert _stored(second, UNTRACKED) is None


async def test_a_named_account_stores_even_an_unknown_code(hass):
    first, second = await _two_accounts(hass, [detail_sample()], [detail_sample()])

    await _call_quiet(
        hass,
        {
            "barcode": UNTRACKED,
            "contents": "Shoes",
            "config_entry_id": second.entry_id,
        },
    )

    assert _stored(first, UNTRACKED) is None
    assert _stored(second, UNTRACKED) == "Shoes"


async def test_a_named_account_limits_a_code_both_accounts_list(hass):
    first, second = await _two_accounts(hass, [detail_sample()], [detail_sample()])

    await _call_quiet(
        hass,
        {
            "barcode": ACTIVE_BARCODE,
            "contents": "Shoes",
            "config_entry_id": first.entry_id,
        },
    )

    assert _stored(first, ACTIVE_BARCODE) == "Shoes"
    assert _stored(second, ACTIVE_BARCODE) is None


async def test_a_named_account_that_is_not_loaded_is_refused(hass):
    await _setup(hass)

    with pytest.raises(ServiceValidationError):
        await _call_quiet(
            hass,
            {"barcode": ACTIVE_BARCODE, "contents": "Shoes", "config_entry_id": "nope"},
        )


async def test_a_single_account_stores_an_unknown_code_without_naming_it(hass):
    entry = await _setup(hass)

    await _call_quiet(hass, {"barcode": UNTRACKED, "contents": "Shoes"})

    assert _stored(entry, UNTRACKED) == "Shoes"
