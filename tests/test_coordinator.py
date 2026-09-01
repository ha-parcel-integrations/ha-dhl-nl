"""Tests for coordinator filter functions and error handling."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.dhl_nl.api import DhlApiError
from custom_components.dhl_nl.const import (
    ACTIVE_CATEGORIES,
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_REFRESH_INTERVAL,
    HOT_INTERVAL_MINUTES,
    KNOWN_CAPABILITIES,
    MID_INTERVAL_MINUTES,
    REFRESH_INTERVAL_AUTO,
    STAGGER_MINUTES,
    ParcelStatus,
)
from custom_components.dhl_nl.coordinator import (
    DhlCoordinator,
    DhlSentShipmentsCoordinator,
    _hottest_tier_minutes,
    _in_quiet_window,
    _next_anchor,
    _next_update_interval,
    _refresh_interval,
    _refresh_setting,
    _stagger_minutes,
)
from custom_components.dhl_nl.parcels import (
    _extract_events,
    build_history,
    filter_active_parcels,
    filter_active_returns,
    filter_active_sent_shipments,
    filter_delivered_parcels,
    filter_delivered_returns,
    filter_delivered_sent_shipments,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    sort_parcels_by_ts,
)

from .payloads import (
    history_parcel_sample,
    parcel_sample,
    shipment_sample,
    track_trace,
)


def _mock_entry(
    filter_type: str = "days",
    filter_amount: int = 7,
    *,
    include_history: bool = False,
    refresh_interval: str | int | None = None,
    entry_id: str = "test-entry",
) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.options = {
        CONF_DELIVERED_FILTER_TYPE: filter_type,
        CONF_DELIVERED_FILTER_AMOUNT: filter_amount,
        CONF_INCLUDE_HISTORY: include_history,
    }
    if refresh_interval is not None:
        entry.options[CONF_REFRESH_INTERVAL] = refresh_interval
    return entry


# ---------------------------------------------------------------------------
# filter_active_parcels
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# map_parcel_status
# ---------------------------------------------------------------------------


def test_receiver_reschedule_maps_to_in_transit_not_problem():
    """A receiver-requested reschedule is benign — it must not show as PROBLEM.

    The specific raw status takes precedence over the INTERVENTION category.
    """
    parcel = {
        "status": "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_ANOTHER_TIME/DATE",
        "category": "INTERVENTION",
    }
    assert map_parcel_status(parcel) == ParcelStatus.IN_TRANSIT


def test_other_intervention_still_maps_to_problem():
    """Unmapped INTERVENTION statuses still fall back to PROBLEM via category."""
    parcel = {"status": "INTERVENTION_PARCEL_DAMAGED", "category": "INTERVENTION"}
    assert map_parcel_status(parcel) == ParcelStatus.PROBLEM


@pytest.mark.parametrize(
    "status,category,expected",
    [
        # Out for delivery — the category only says IN_DELIVERY (IN_TRANSIT).
        ("LOAD_VEHICLE", "IN_DELIVERY", ParcelStatus.OUT_FOR_DELIVERY),
        ("PARCEL_INTO_FALLBACK", "IN_DELIVERY", ParcelStatus.OUT_FOR_DELIVERY),
        # Ready for collection at a ServicePoint / depot.
        ("DELIVERED_AT_PARCELSTATION", "UNDERWAY", ParcelStatus.AT_PICKUP_POINT),
        (
            "NOTIFICATION_FOR_PARCELSTATION_COLLECTION_HAS_BEEN_SENT",
            "IN_DELIVERY",
            ParcelStatus.AT_PICKUP_POINT,
        ),
        ("AWAITING_RECEIVER_COLLECTION", "UNDERWAY", ParcelStatus.AT_PICKUP_POINT),
        ("REMINDER_FOR_COLLECTION_SENT_SMS", "UNDERWAY", ParcelStatus.AT_PICKUP_POINT),
        # Collected by the recipient — terminal.
        ("SHIPMENT_COLLECTED", "UNDERWAY", ParcelStatus.DELIVERED),
        ("COLLECTED_AT_PARCELSTATION", "UNDERWAY", ParcelStatus.DELIVERED),
        # Returning — the category would mislabel these IN_TRANSIT or PROBLEM.
        ("RETURNED_TO_SHIPPER", "UNDERWAY", ParcelStatus.RETURNING),
        ("REFUSED_RETURN", "INTERVENTION", ParcelStatus.RETURNING),
        ("RECEIVER_UNKNOWN_RETURN", "INTERVENTION", ParcelStatus.RETURNING),
        ("STORAGE_PERIOD_ENDED_AT_PARCELSHOP", "UNDERWAY", ParcelStatus.RETURNING),
        ("ON_ROUTE_TO_SHIPPER", "UNDERWAY", ParcelStatus.RETURNING),
    ],
)
def test_granular_status_overrides_category(status, category, expected):
    """Finer parcel-nl statuses take precedence over the coarse category —
    without them, returns/pickups/out-for-delivery are mislabelled.
    """
    assert map_parcel_status({"status": status, "category": category}) == expected


def test_unmapped_status_warns_even_when_category_fallback_succeeds(caplog):
    """An unmapped status must not hide behind a coincidentally-correct category.

    Regression for issue #11: a locker notification status fell through to
    IN_DELIVERY -> IN_TRANSIT with no log line, so the wrong-but-plausible
    result went unnoticed until a user compared against the DHL app.
    """
    parcel = {"status": "SOME_NEW_LOCKER_STATUS", "category": "IN_DELIVERY"}
    assert map_parcel_status(parcel) == ParcelStatus.IN_TRANSIT
    assert "SOME_NEW_LOCKER_STATUS" in caplog.text
    assert "issues/new" in caplog.text


def test_fully_unmapped_status_returns_unknown_and_warns(caplog):
    parcel = {"status": "WARP_DRIVE_ENGAGED", "category": "ZZTOP"}
    assert map_parcel_status(parcel) == ParcelStatus.UNKNOWN
    assert "WARP_DRIVE_ENGAGED" in caplog.text
    assert "issues/new" in caplog.text


@pytest.mark.parametrize(
    "status",
    ["PRENOTIFICATION_RECEIVED", "DATA_RECEIVED_WITH_PREFIX_LABEL"],
)
def test_data_received_variants_map_to_registered_without_warning(status, caplog):
    """Regression for issue #12: these DATA_RECEIVED variants already resolved
    to REGISTERED via the category fallback, but kept tripping the
    unmapped-status warning. Now mapped explicitly, so no warning fires.
    """
    parcel = {"status": status, "category": "DATA_RECEIVED"}
    assert map_parcel_status(parcel) == ParcelStatus.REGISTERED
    assert status not in caplog.text


@pytest.mark.parametrize(
    "status,category,expected",
    [
        ("DELIVERED", "DELIVERED", ParcelStatus.DELIVERED),
        ("PARCEL_ARRIVED_AT_LOCAL_DEPOT", "UNDERWAY", ParcelStatus.IN_TRANSIT),
        ("PARCEL_SORTED_AT_HUB", "UNDERWAY", ParcelStatus.IN_TRANSIT),
    ],
)
def test_hub_and_terminal_events_map_without_warning(status, category, expected, caplog):
    """These already resolved correctly via the category fallback, but kept
    tripping the unmapped-status warning on ordinary history events. Now
    mapped explicitly, so no warning fires.
    """
    parcel = {"status": status, "category": category}
    assert map_parcel_status(parcel) == expected
    assert status not in caplog.text


def test_active_parcel_is_included():
    assert filter_active_parcels([parcel_sample("IN_DELIVERY")]) != []


def test_delivered_parcel_is_excluded():
    assert filter_active_parcels([parcel_sample("DELIVERED")]) == []


def test_return_parcel_is_excluded():
    assert filter_active_parcels([parcel_sample("IN_DELIVERY", is_return=True)]) == []


def test_all_active_categories_pass():
    parcels = [parcel_sample(cat) for cat in ACTIVE_CATEGORIES]
    assert len(filter_active_parcels(parcels)) == len(ACTIVE_CATEGORIES)


def test_mixed_parcels_filtered_correctly():
    parcels = [
        parcel_sample("IN_DELIVERY"),
        parcel_sample("DELIVERED"),
        parcel_sample("IN_DELIVERY", is_return=True),
        parcel_sample("UNDERWAY"),
    ]
    result = filter_active_parcels(parcels)
    assert len(result) == 2


def test_empty_list_returns_empty():
    assert filter_active_parcels([]) == []


# ---------------------------------------------------------------------------
# filter_delivered_parcels
# ---------------------------------------------------------------------------


def test_delivered_parcel_is_included():
    assert filter_delivered_parcels([parcel_sample("DELIVERED")]) != []


def test_active_parcel_excluded_from_delivered():
    assert filter_delivered_parcels([parcel_sample("IN_DELIVERY")]) == []


def test_return_parcel_excluded_from_delivered():
    assert filter_delivered_parcels([parcel_sample("DELIVERED", is_return=True)]) == []


def test_delivered_filters_only_non_return_delivered():
    parcels = [
        parcel_sample("DELIVERED"),
        parcel_sample("DELIVERED", is_return=True),
        parcel_sample("IN_DELIVERY"),
    ]
    assert len(filter_delivered_parcels(parcels)) == 1


# ---------------------------------------------------------------------------
# filter_active_returns / filter_delivered_returns
# ---------------------------------------------------------------------------


def test_active_return_is_included():
    assert filter_active_returns([parcel_sample("UNDERWAY", is_return=True)]) != []


def test_non_return_excluded_from_active_returns():
    assert filter_active_returns([parcel_sample("UNDERWAY", is_return=False)]) == []


def test_delivered_return_excluded_from_active_returns():
    assert filter_active_returns([parcel_sample("DELIVERED", is_return=True)]) == []


def test_delivered_return_is_included():
    assert filter_delivered_returns([parcel_sample("DELIVERED", is_return=True)]) != []


def test_non_return_excluded_from_delivered_returns():
    assert filter_delivered_returns([parcel_sample("DELIVERED", is_return=False)]) == []


def test_active_return_excluded_from_delivered_returns():
    assert filter_delivered_returns([parcel_sample("UNDERWAY", is_return=True)]) == []


def test_mixed_parcels_split_correctly_between_incoming_and_returns():
    parcels = [
        parcel_sample("IN_DELIVERY", barcode="incoming-active"),
        parcel_sample("DELIVERED", barcode="incoming-delivered"),
        parcel_sample("UNDERWAY", is_return=True, barcode="return-active"),
        parcel_sample("DELIVERED", is_return=True, barcode="return-delivered"),
    ]
    assert [p["barcode"] for p in filter_active_parcels(parcels)] == ["incoming-active"]
    assert [p["barcode"] for p in filter_delivered_parcels(parcels)] == ["incoming-delivered"]
    assert [p["barcode"] for p in filter_active_returns(parcels)] == ["return-active"]
    assert [p["barcode"] for p in filter_delivered_returns(parcels)] == ["return-delivered"]


def test_missing_is_return_field_falls_back_to_incoming(caplog):
    parcel = parcel_sample("IN_DELIVERY", barcode="no-isreturn-field")
    del parcel["isReturn"]

    assert filter_active_parcels([parcel]) == [parcel]
    assert filter_active_returns([parcel]) == []
    assert "no-isreturn-field" in caplog.text
    assert "isReturn" in caplog.text


def test_missing_is_return_field_logged_once_per_barcode(caplog):
    parcel = parcel_sample("IN_DELIVERY", barcode="repeat-barcode")
    del parcel["isReturn"]

    filter_active_parcels([parcel])
    caplog.clear()
    filter_active_parcels([parcel])

    assert "repeat-barcode" not in caplog.text


# ---------------------------------------------------------------------------
# filter_active_sent_shipments
# ---------------------------------------------------------------------------


def test_active_outgoing_shipment_is_included():
    assert filter_active_sent_shipments([shipment_sample("IN_DELIVERY")]) != []


def test_delivered_shipment_is_excluded():
    assert filter_active_sent_shipments([shipment_sample("DELIVERED")]) == []


def test_non_outgoing_type_is_excluded():
    assert filter_active_sent_shipments([shipment_sample("IN_DELIVERY", shipment_type="incoming")]) == []


# ---------------------------------------------------------------------------
# filter_delivered_sent_shipments
# ---------------------------------------------------------------------------


def test_delivered_outgoing_shipment_is_included():
    assert filter_delivered_sent_shipments([shipment_sample("DELIVERED")]) != []


def test_active_outgoing_shipment_excluded_from_delivered():
    assert filter_delivered_sent_shipments([shipment_sample("IN_DELIVERY")]) == []


def test_non_outgoing_type_excluded_from_delivered():
    assert filter_delivered_sent_shipments([shipment_sample("DELIVERED", shipment_type="incoming")]) == []


# ---------------------------------------------------------------------------
# DhlSentShipmentsCoordinator — active + delivered
# ---------------------------------------------------------------------------


async def test_sent_shipments_coordinator_populates_active_and_delivered(hass):
    client = MagicMock()
    client.async_get_sent_shipments = AsyncMock(return_value=[
        shipment_sample("IN_DELIVERY"),
        shipment_sample("DELIVERED"),
        shipment_sample("IN_DELIVERY", shipment_type="incoming"),
    ])

    coordinator = DhlSentShipmentsCoordinator(hass, client, _mock_entry())
    result = await coordinator._async_update_data()

    assert len(result) == 1
    assert result[0]["raw"]["category"] == "IN_DELIVERY"
    assert len(coordinator.delivered) == 1
    assert coordinator.delivered[0]["raw"]["category"] == "DELIVERED"
    assert coordinator.delivered[0]["delivered"] is True


async def test_sent_shipments_coordinator_delivered_filter_applies(hass):
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    shipment = shipment_sample("DELIVERED")
    shipment["receivingTimeIndication"] = {"indicationType": "MomentIndication", "moment": old}
    client = MagicMock()
    client.async_get_sent_shipments = AsyncMock(return_value=[shipment])

    coordinator = DhlSentShipmentsCoordinator(hass, client, _mock_entry("days", 7))
    await coordinator._async_update_data()

    assert coordinator.delivered == []


# ---------------------------------------------------------------------------
# DhlCoordinator._apply_delivered_filter — days mode
# ---------------------------------------------------------------------------


async def test_delivered_filter_days_excludes_old_parcels(hass):
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    parcels = [
        parcel_sample("DELIVERED", moment=old),
        parcel_sample("DELIVERED", moment=recent),
    ]
    coordinator = DhlCoordinator(hass, MagicMock(), _mock_entry("days", 7))
    result = coordinator._apply_delivered_filter(parcels)
    assert len(result) == 1
    assert result[0]["receivingTimeIndication"]["moment"] == recent


async def test_delivered_filter_days_includes_parcel_without_date(hass):
    parcels = [parcel_sample("DELIVERED")]
    coordinator = DhlCoordinator(hass, MagicMock(), _mock_entry("days", 7))
    result = coordinator._apply_delivered_filter(parcels)
    assert len(result) == 1


async def test_delivered_filter_days_all_recent(hass):
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    parcels = [parcel_sample("DELIVERED", moment=recent)] * 5
    coordinator = DhlCoordinator(hass, MagicMock(), _mock_entry("days", 7))
    assert len(coordinator._apply_delivered_filter(parcels)) == 5


# ---------------------------------------------------------------------------
# DhlCoordinator._apply_delivered_filter — parcels mode
# ---------------------------------------------------------------------------


async def test_delivered_filter_parcels_limits_count(hass):
    parcels = [parcel_sample("DELIVERED")] * 10
    coordinator = DhlCoordinator(hass, MagicMock(), _mock_entry("parcels", 3))
    result = coordinator._apply_delivered_filter(parcels)
    assert len(result) == 3


async def test_delivered_filter_parcels_fewer_than_limit(hass):
    parcels = [parcel_sample("DELIVERED")] * 2
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
        parcel_sample("IN_DELIVERY"),
        parcel_sample("DELIVERED"),
        parcel_sample("IN_DELIVERY", is_return=True),
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
        "receiver": {"name": "J. Doe"},
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
    assert result["receiver"] == "J. Doe"
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
    assert result["receiver"] is None
    assert result["pickup"] is False
    assert result["pickup_point"] is None
    assert result["url"] is None
    assert result["weight"] is None
    assert result["dimensions"] is None


def test_normalize_always_carries_none_weight_and_dimensions_on_dhl():
    """DHL doesn't expose weight/dimensions in any endpoint we know of, so the
    canonical fields are always None — but they MUST be present so the
    aggregator and cross-carrier cards can rely on the keys existing.
    """
    parcel = {"barcode": "ABC", "category": "IN_DELIVERY", "destination": {}}
    result = normalize_parcel(parcel)
    assert "weight" in result and result["weight"] is None
    assert "dimensions" in result and result["dimensions"] is None


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_capabilities_omit_weight_and_dimensions():
    """DHL never exposes these — CAPABILITIES must not claim otherwise."""
    assert "weight" not in CAPABILITIES
    assert "dimensions" not in CAPABILITIES


def test_normalize_constructs_tracking_url():
    parcel = {
        "barcode": "3SXXXXXXXXXXXXXXXXX",
        "category": "IN_DELIVERY",
        "receiver": {"address": {"postalCode": "1234 AB"}},
        "destination": {"address": {"postalCode": "1234 AB"}},
    }
    result = normalize_parcel(parcel)
    assert result["url"] == (
        "https://my.dhlecommerce.nl/portal/tracktrace/3SXXXXXXXXXXXXXXXXX/1234AB"
    )


def test_tracking_url_uses_receiver_postcode_not_destination():
    """Pickup-point deliveries have a ServicePoint destination postcode that the
    portal rejects; the receiver's postcode is the one that resolves (issue #9).
    """
    parcel = {
        "barcode": "3SXXXXXXXXXXXXXXXXX",
        "category": "IN_DELIVERY",
        "receiver": {"address": {"postalCode": "1234 AB"}},
        "destination": {
            "locationType": "SERVICEPOINT",
            "address": {"postalCode": "5678 CD"},
        },
    }
    assert normalize_parcel(parcel)["url"] == (
        "https://my.dhlecommerce.nl/portal/tracktrace/3SXXXXXXXXXXXXXXXXX/1234AB"
    )


def test_tracking_url_falls_back_to_destination_postcode():
    """When the receiver carries no postcode, the destination is still used."""
    parcel = {
        "barcode": "3SXXXXXXXXXXXXXXXXX",
        "category": "IN_DELIVERY",
        "destination": {"address": {"postalCode": "1234 AB"}},
    }
    assert normalize_parcel(parcel)["url"] == (
        "https://my.dhlecommerce.nl/portal/tracktrace/3SXXXXXXXXXXXXXXXXX/1234AB"
    )


def test_normalize_url_none_when_postcode_missing():
    parcel = {
        "barcode": "3SXXXXXXXXXXXXXXXXX",
        "category": "IN_DELIVERY",
        "receiver": {"address": {}},
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
        parcel_sample("IN_DELIVERY"),
        parcel_sample("DELIVERED", moment=recent),
        parcel_sample("DELIVERED", is_return=True, moment=recent),
    ])

    coordinator = DhlCoordinator(hass, client, _mock_entry("days", 7))
    await coordinator._async_update_data()

    assert len(coordinator.delivered) == 1
    assert coordinator.delivered[0]["raw"]["category"] == "DELIVERED"


async def test_coordinator_populates_returning_and_delivered_outgoing(hass):
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value=[
        parcel_sample("IN_DELIVERY", barcode="incoming"),
        parcel_sample("UNDERWAY", is_return=True, barcode="return-underway"),
        parcel_sample("DELIVERED", is_return=True, moment=recent, barcode="return-delivered"),
    ])

    coordinator = DhlCoordinator(hass, client, _mock_entry("days", 7))
    result = await coordinator._async_update_data()

    # The main return value (coordinator.data) stays incoming-only.
    assert [p["barcode"] for p in result] == ["incoming"]

    assert len(coordinator.returning) == 1
    assert coordinator.returning[0]["barcode"] == "return-underway"
    assert coordinator.returning[0]["carrier"] == "DHL"

    assert len(coordinator.delivered_outgoing) == 1
    assert coordinator.delivered_outgoing[0]["barcode"] == "return-delivered"
    assert coordinator.delivered_outgoing[0]["delivered"] is True


async def test_returning_and_delivered_outgoing_empty_without_returns(hass):
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value=[parcel_sample("IN_DELIVERY")])

    coordinator = DhlCoordinator(hass, client, _mock_entry())
    await coordinator._async_update_data()

    assert coordinator.returning == []
    assert coordinator.delivered_outgoing == []


# ---------------------------------------------------------------------------
# Outgoing event firing — outgoing_parcel_status_changed / _delivered
# ---------------------------------------------------------------------------


async def test_no_outgoing_events_on_first_refresh(hass):
    """The first refresh seeds outgoing state silently — no outgoing events."""
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value=[
        parcel_sample("UNDERWAY", is_return=True, barcode="R"),
    ])

    fired: list = []
    hass.bus.async_listen("dhl_nl_outgoing_parcel_status_changed", lambda e: fired.append(e))
    hass.bus.async_listen("dhl_nl_outgoing_parcel_delivered", lambda e: fired.append(e))

    coordinator = DhlCoordinator(hass, client, _mock_entry())
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert fired == []


async def test_outgoing_status_changed_when_return_status_transitions(hass):
    """A return whose active status changes fires outgoing_parcel_status_changed."""
    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=[
        [parcel_sample("DATA_RECEIVED", is_return=True, barcode="R")],
        [parcel_sample("UNDERWAY", is_return=True, barcode="R")],
    ])

    changed: list = []
    hass.bus.async_listen(
        "dhl_nl_outgoing_parcel_status_changed", lambda e: changed.append(e.data)
    )

    coordinator = DhlCoordinator(hass, client, _mock_entry())
    await coordinator._async_update_data()
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(changed) == 1
    assert changed[0]["barcode"] == "R"
    assert changed[0]["old_status"] == ParcelStatus.REGISTERED
    assert changed[0]["new_status"] == ParcelStatus.IN_TRANSIT


async def test_outgoing_delivered_when_return_is_delivered(hass):
    """A return that transitions to delivered fires outgoing_parcel_delivered
    and NOT outgoing_parcel_status_changed (delivered takes precedence).
    """
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=[
        [parcel_sample("UNDERWAY", is_return=True, barcode="R")],
        [parcel_sample("DELIVERED", is_return=True, moment=recent, barcode="R")],
    ])

    delivered: list = []
    changed: list = []
    hass.bus.async_listen(
        "dhl_nl_outgoing_parcel_delivered", lambda e: delivered.append(e.data)
    )
    hass.bus.async_listen(
        "dhl_nl_outgoing_parcel_status_changed", lambda e: changed.append(e.data)
    )

    coordinator = DhlCoordinator(hass, client, _mock_entry("days", 7))
    await coordinator._async_update_data()
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(delivered) == 1
    assert delivered[0]["barcode"] == "R"
    assert changed == []


async def test_no_outgoing_event_for_already_delivered_return(hass):
    """A return that is delivered on both refreshes never fires (no change)."""
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value=[
        parcel_sample("DELIVERED", is_return=True, moment=recent, barcode="R"),
    ])

    fired: list = []
    hass.bus.async_listen("dhl_nl_outgoing_parcel_delivered", lambda e: fired.append(e))
    hass.bus.async_listen("dhl_nl_outgoing_parcel_status_changed", lambda e: fired.append(e))

    coordinator = DhlCoordinator(hass, client, _mock_entry("days", 7))
    await coordinator._async_update_data()
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert fired == []


# ---------------------------------------------------------------------------
# Event firing — parcel_registered and parcel_status_changed
# ---------------------------------------------------------------------------


async def test_no_events_on_first_refresh(hass):
    """The first refresh seeds known state silently — no events."""
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value=[
        parcel_sample("IN_DELIVERY", barcode="A"),
        parcel_sample("IN_DELIVERY", barcode="B"),
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
        [parcel_sample("IN_DELIVERY", barcode="A")],
        [parcel_sample("IN_DELIVERY", barcode="A"), parcel_sample("IN_DELIVERY", barcode="B")],
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


async def test_delivered_event_when_parcel_transitions_to_delivered(hass):
    """The hop to delivered fires parcel_delivered — and never status_changed.

    Incoming events run over the active + delivered set combined, so the
    terminal transition is visible even though the parcel leaves the
    active list. The dedicated event takes precedence over status_changed.
    """
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=[
        [parcel_sample("IN_DELIVERY", barcode="A")],
        [parcel_sample("DELIVERED", barcode="A", moment=recent)],
    ])

    delivered: list = []
    changed: list = []
    hass.bus.async_listen(
        "dhl_nl_parcel_delivered", lambda e: delivered.append(e.data)
    )
    hass.bus.async_listen(
        "dhl_nl_parcel_status_changed", lambda e: changed.append(e.data)
    )

    coordinator = DhlCoordinator(hass, client, _mock_entry())
    await coordinator._async_update_data()
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert changed == []
    assert len(delivered) == 1
    assert delivered[0]["barcode"] == "A"
    assert delivered[0]["status"] == ParcelStatus.DELIVERED


async def test_no_events_for_new_already_delivered_parcel(hass):
    """A barcode first seen already delivered fires neither registered nor delivered."""
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=[
        [parcel_sample("IN_DELIVERY", barcode="A")],
        [
            parcel_sample("IN_DELIVERY", barcode="A"),
            parcel_sample("DELIVERED", barcode="B", moment=recent),
        ],
    ])

    fired: list = []
    hass.bus.async_listen("dhl_nl_parcel_registered", lambda e: fired.append(e))
    hass.bus.async_listen("dhl_nl_parcel_delivered", lambda e: fired.append(e))

    coordinator = DhlCoordinator(hass, client, _mock_entry())
    await coordinator._async_update_data()
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert fired == []


async def test_status_changed_event_when_active_status_transitions(hass):
    """When an active parcel changes from one IN_TRANSIT status to another."""
    from custom_components.dhl_nl.const import ParcelStatus

    p1 = parcel_sample("IN_DELIVERY", barcode="A")
    p1["status"] = "DATA_RECEIVED"  # raw status — maps to REGISTERED via fallback
    p1["category"] = "DATA_RECEIVED"

    p2 = parcel_sample("IN_DELIVERY", barcode="A")
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


# ---------------------------------------------------------------------------
# Event firing — parcel_delivery_time_changed
# ---------------------------------------------------------------------------


async def test_delivery_time_changed_fires_when_planned_time_appears(hass):
    """A barcode that gains a planned_from value fires delivery_time_changed."""
    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=[
        [parcel_sample("IN_DELIVERY", barcode="A")],
        [parcel_sample("IN_DELIVERY", barcode="A", moment="2026-06-27T10:00:00+02:00")],
    ])

    changed: list = []
    hass.bus.async_listen(
        "dhl_nl_parcel_delivery_time_changed", lambda e: changed.append(e.data)
    )

    coordinator = DhlCoordinator(hass, client, _mock_entry())
    await coordinator._async_update_data()
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(changed) == 1
    assert changed[0]["barcode"] == "A"
    assert changed[0]["old_planned_from"] is None
    assert changed[0]["new_planned_from"] == "2026-06-27T10:00:00+02:00"


async def test_delivery_time_changed_fires_when_planned_time_shifts(hass):
    """A barcode whose planned_from changes to a new value fires the event."""
    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=[
        [parcel_sample("IN_DELIVERY", barcode="A", moment="2026-06-27T10:00:00+02:00")],
        [parcel_sample("IN_DELIVERY", barcode="A", moment="2026-06-27T14:00:00+02:00")],
    ])

    changed: list = []
    hass.bus.async_listen(
        "dhl_nl_parcel_delivery_time_changed", lambda e: changed.append(e.data)
    )

    coordinator = DhlCoordinator(hass, client, _mock_entry())
    await coordinator._async_update_data()
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(changed) == 1
    assert changed[0]["old_planned_from"] == "2026-06-27T10:00:00+02:00"
    assert changed[0]["new_planned_from"] == "2026-06-27T14:00:00+02:00"


async def test_no_delivery_time_changed_event_when_planned_time_clears(hass):
    """Value → null transitions are silent (don't page users on lost ETAs)."""
    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=[
        [parcel_sample("IN_DELIVERY", barcode="A", moment="2026-06-27T10:00:00+02:00")],
        [parcel_sample("IN_DELIVERY", barcode="A")],
    ])

    changed: list = []
    hass.bus.async_listen(
        "dhl_nl_parcel_delivery_time_changed", lambda e: changed.append(e.data)
    )

    coordinator = DhlCoordinator(hass, client, _mock_entry())
    await coordinator._async_update_data()
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert changed == []


async def test_no_delivery_time_changed_event_when_planned_time_unchanged(hass):
    """An unchanged planned_from does not fire the event."""
    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=[
        [parcel_sample("IN_DELIVERY", barcode="A", moment="2026-06-27T10:00:00+02:00")],
        [parcel_sample("IN_DELIVERY", barcode="A", moment="2026-06-27T10:00:00+02:00")],
    ])

    changed: list = []
    hass.bus.async_listen(
        "dhl_nl_parcel_delivery_time_changed", lambda e: changed.append(e.data)
    )

    coordinator = DhlCoordinator(hass, client, _mock_entry())
    await coordinator._async_update_data()
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert changed == []


# ---------------------------------------------------------------------------
# _refresh_interval
# ---------------------------------------------------------------------------


def test_refresh_interval_defaults_to_30_minutes_when_option_unset():
    entry = MagicMock()
    entry.options = {}
    assert _refresh_interval(entry).total_seconds() == 30 * 60


def test_refresh_interval_reads_minutes_from_options():
    entry = MagicMock()
    entry.options = {"refresh_interval": 60}
    assert _refresh_interval(entry).total_seconds() == 60 * 60


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def _norm(barcode: str, planned_from: str | None = None, delivered_at: str | None = None) -> dict:
    return {
        "barcode": barcode,
        "planned_from": planned_from,
        "delivered_at": delivered_at,
    }


def test_sort_orders_ascending_by_planned_from():
    parcels = [
        _norm("late", planned_from="2026-06-15T10:00:00+00:00"),
        _norm("early", planned_from="2026-06-13T08:00:00+00:00"),
        _norm("mid", planned_from="2026-06-14T12:00:00+00:00"),
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["early", "mid", "late"]


def test_sort_orders_descending_for_delivered_at():
    parcels = [
        _norm("oldest", delivered_at="2026-06-13T08:00:00+00:00"),
        _norm("newest", delivered_at="2026-06-15T10:00:00+00:00"),
        _norm("mid", delivered_at="2026-06-14T12:00:00+00:00"),
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)]
    assert ordered == ["newest", "mid", "oldest"]


def test_sort_keeps_missing_or_garbage_timestamps_at_end():
    parcels = [
        _norm("no-ts"),
        _norm("garbage", planned_from="not-a-date"),
        _norm("early", planned_from="2026-06-13T08:00:00+00:00"),
        _norm("late", planned_from="2026-06-15T10:00:00+00:00"),
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered[:2] == ["early", "late"]
    assert set(ordered[2:]) == {"no-ts", "garbage"}


def test_sort_handles_z_suffix_timestamps():
    parcels = [
        _norm("a", planned_from="2026-06-15T10:00:00Z"),
        _norm("b", planned_from="2026-06-13T10:00:00Z"),
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["b", "a"]


def test_sort_empty_input_returns_empty_list():
    assert sort_parcels_by_ts([], "planned_from") == []


# ---------------------------------------------------------------------------
# map_event_status — reuses the parcel maps (status key, then phase)
# ---------------------------------------------------------------------------


def test_map_event_status_granular_key_wins():
    # OUT_FOR_DELIVERY is in _STATUS_MAP; the phase would only give in_transit.
    assert map_event_status("OUT_FOR_DELIVERY", "IN_DELIVERY") == ParcelStatus.OUT_FOR_DELIVERY


def test_map_event_status_falls_back_to_phase():
    # PARCEL_SORTED_AT_HUB isn't in _STATUS_MAP → phase UNDERWAY → in_transit.
    assert map_event_status("PARCEL_SORTED_AT_HUB", "UNDERWAY") == ParcelStatus.IN_TRANSIT
    assert map_event_status("PRENOTIFICATION_RECEIVED", "DATA_RECEIVED") == ParcelStatus.REGISTERED
    assert map_event_status("DELIVERED", "DELIVERED") == ParcelStatus.DELIVERED


def test_map_event_status_warns_even_when_phase_fallback_succeeds(caplog):
    # Distinct key from test_map_event_status_falls_back_to_phase — the
    # one-shot log guard is process-global, reusing a key would suppress it.
    assert map_event_status("PARCEL_LEFT_HUB", "UNDERWAY") == ParcelStatus.IN_TRANSIT
    assert "PARCEL_LEFT_HUB" in caplog.text
    assert "issues/new" in caplog.text


def test_map_event_status_pickup_point_key():
    assert map_event_status(
        "NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT", "IN_DELIVERY"
    ) == ParcelStatus.AT_PICKUP_POINT


def test_map_event_status_none_for_unmapped(caplog):
    assert map_event_status("WARP_DRIVE_ENGAGED", "ZZTOP") is None
    assert "WARP_DRIVE_ENGAGED" in caplog.text
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# _extract_events / build_history
# ---------------------------------------------------------------------------


def test_extract_events_flattens_phases_with_phase_tag():
    pairs = _extract_events(track_trace())
    assert len(pairs) == 5
    # Each event carries its parent phase.
    assert all(phase for _, phase in pairs)
    assert ("DELIVERED" in (phase for _, phase in pairs))


def test_extract_events_empty_for_falsy_or_shapeless():
    assert _extract_events(None) == []
    assert _extract_events([]) == []
    assert _extract_events([{"view": {}}]) == []


def test_build_history_orders_oldest_first_and_maps():
    history = build_history(track_trace())
    assert [e["status"] for e in history] == [
        ParcelStatus.REGISTERED,
        ParcelStatus.IN_TRANSIT,
        ParcelStatus.IN_TRANSIT,
        ParcelStatus.OUT_FOR_DELIVERY,
        ParcelStatus.DELIVERED,
    ]
    # raw_status is the event key (DHL has no human event text).
    assert history[0]["raw_status"] == "PRENOTIFICATION_RECEIVED"
    assert history[-1]["raw_status"] == "DELIVERED"
    assert set(history[0]) == {"timestamp", "status", "raw_status"}


def test_build_history_caps_to_max_events():
    events = [
        {"timestamp": f"2026-06-{day:02d}T10:00:00Z", "key": "PARCEL_SORTED_AT_HUB", "exception": False}
        for day in range(1, 26)
    ]
    track_trace = [{"view": {"phases": [{"phase": "UNDERWAY", "events": events}]}}]
    history = build_history(track_trace)
    assert len(history) == 20
    assert history[0]["timestamp"] == "2026-06-06T10:00:00Z"


def test_build_history_respects_custom_cap():
    assert len(build_history(track_trace(), max_events=2)) == 2


def test_build_history_skips_events_without_timestamp():
    track_trace = [{"view": {"phases": [{"phase": "UNDERWAY", "events": [
        {"key": "PARCEL_SORTED_AT_HUB", "exception": False},
        {"timestamp": "2026-06-24T02:00:00Z", "key": "PARCEL_SORTED_AT_HUB", "exception": False},
    ]}]}}]
    assert len(build_history(track_trace)) == 1


def test_build_history_empty_for_no_data():
    assert build_history(None) == []
    assert build_history([]) == []


def test_build_history_handles_naive_and_unparseable_timestamps():
    track_trace = [{"view": {"phases": [{"phase": "UNDERWAY", "events": [
        {"timestamp": "garbage", "key": "PARCEL_SORTED_AT_HUB", "exception": False},
        {"timestamp": "2026-06-24T02:00:00", "key": "PARCEL_SORTED_AT_HUB", "exception": False},  # naive
    ]}]}}]
    history = build_history(track_trace)
    # The naive (parseable) entry sorts ahead of the unparseable one.
    assert history[0]["timestamp"] == "2026-06-24T02:00:00"
    assert history[-1]["timestamp"] == "garbage"


# ---------------------------------------------------------------------------
# normalize_parcel — history field
# ---------------------------------------------------------------------------


def test_normalize_parcel_history_defaults_to_none():
    assert normalize_parcel(parcel_sample("IN_DELIVERY"))["history"] is None


def test_normalize_parcel_history_passes_through_top_level():
    events = [{"timestamp": "2026-06-24T17:23:13Z", "status": "delivered", "raw_status": "DELIVERED"}]
    normalized = normalize_parcel(parcel_sample("DELIVERED"), history=events)
    assert normalized["history"] == events
    # Top-level so it survives the aggregator's strip_raw(); not under raw.
    assert "history" not in normalized["raw"]


# ---------------------------------------------------------------------------
# DhlCoordinator._enrich_history
# ---------------------------------------------------------------------------


async def test_enrich_history_fetches_and_caches_when_option_on(hass):
    client = MagicMock()
    client.async_get_track_trace = AsyncMock(return_value=track_trace())
    coordinator = DhlCoordinator(hass, client, _mock_entry(include_history=True))

    await coordinator._enrich_history([history_parcel_sample()])

    # Postcode is whitespace-stripped; uuid + barcode passed through.
    client.async_get_track_trace.assert_awaited_once_with("JX1", "1234AB", "uuid-1")
    cached = coordinator._history_cache["JX1"]
    assert cached["history"][-1]["status"] == ParcelStatus.DELIVERED
    assert cached["_raw_status"] == "OUT_FOR_DELIVERY"


async def test_enrich_history_noop_when_option_off(hass):
    client = MagicMock()
    client.async_get_track_trace = AsyncMock(return_value=track_trace())
    coordinator = DhlCoordinator(hass, client, _mock_entry(include_history=False))

    await coordinator._enrich_history([history_parcel_sample()])

    client.async_get_track_trace.assert_not_called()
    assert coordinator._history_cache == {}


async def test_enrich_history_skips_refetch_when_status_unchanged(hass):
    client = MagicMock()
    client.async_get_track_trace = AsyncMock(return_value=track_trace())
    coordinator = DhlCoordinator(hass, client, _mock_entry(include_history=True))
    coordinator._history_cache = {"JX1": {"history": [], "_raw_status": "OUT_FOR_DELIVERY"}}

    await coordinator._enrich_history([history_parcel_sample(status="OUT_FOR_DELIVERY")])

    client.async_get_track_trace.assert_not_called()


async def test_enrich_history_refetches_on_status_change(hass):
    client = MagicMock()
    client.async_get_track_trace = AsyncMock(return_value=track_trace())
    coordinator = DhlCoordinator(hass, client, _mock_entry(include_history=True))
    coordinator._history_cache = {"JX1": {"history": [], "_raw_status": "PARCEL_SORTED_AT_HUB"}}

    await coordinator._enrich_history([history_parcel_sample(status="OUT_FOR_DELIVERY")])

    client.async_get_track_trace.assert_awaited_once()
    assert coordinator._history_cache["JX1"]["_raw_status"] == "OUT_FOR_DELIVERY"


async def test_enrich_history_skips_parcel_without_postcode_or_uuid(hass):
    client = MagicMock()
    client.async_get_track_trace = AsyncMock(return_value=track_trace())
    coordinator = DhlCoordinator(hass, client, _mock_entry(include_history=True))

    no_postcode = {"barcode": "JX2", "parcelId": "u", "status": "X", "receiver": {"address": {}}}
    no_uuid = {"barcode": "JX3", "status": "X", "receiver": {"address": {"postalCode": "1000AA"}}}
    no_barcode = {"parcelId": "u", "status": "X", "receiver": {"address": {"postalCode": "1000AA"}}}
    await coordinator._enrich_history([no_postcode, no_uuid, no_barcode])

    client.async_get_track_trace.assert_not_called()


async def test_enrich_history_best_effort_leaves_cache_on_none(hass):
    client = MagicMock()
    client.async_get_track_trace = AsyncMock(return_value=None)
    coordinator = DhlCoordinator(hass, client, _mock_entry(include_history=True))

    await coordinator._enrich_history([history_parcel_sample()])

    # A None (failed) response must not write a bogus cache entry.
    assert coordinator._history_cache == {}


# ---------------------------------------------------------------------------
# Dynamic polling (Section 2.2, account-based) — pure helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc


def test_refresh_interval_starts_hot_when_auto():
    entry = MagicMock()
    entry.options = {CONF_REFRESH_INTERVAL: REFRESH_INTERVAL_AUTO}
    assert _refresh_interval(entry).total_seconds() == HOT_INTERVAL_MINUTES * 60


def test_refresh_setting_passes_through_auto():
    entry = MagicMock()
    entry.options = {CONF_REFRESH_INTERVAL: REFRESH_INTERVAL_AUTO}
    assert _refresh_setting(entry) == REFRESH_INTERVAL_AUTO


def test_quiet_window_is_midnight_to_six():
    assert _in_quiet_window(datetime(2026, 1, 1, 0, 0, tzinfo=UTC))
    assert _in_quiet_window(datetime(2026, 1, 1, 5, 59, tzinfo=UTC))
    assert not _in_quiet_window(datetime(2026, 1, 1, 6, 0, tzinfo=UTC))
    assert not _in_quiet_window(datetime(2026, 1, 1, 23, 59, tzinfo=UTC))


def test_next_anchor_before_six_is_six_today():
    now = datetime(2026, 1, 1, 2, 30, tzinfo=UTC)
    assert _next_anchor(now) == datetime(2026, 1, 1, 6, 0, tzinfo=UTC)


def test_next_anchor_after_six_is_midnight_tomorrow():
    now = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
    assert _next_anchor(now) == datetime(2026, 1, 2, 0, 0, tzinfo=UTC)


def test_stagger_is_stable_and_bounded():
    a = _stagger_minutes("entry-1")
    b = _stagger_minutes("entry-1")
    c = _stagger_minutes("entry-2")
    assert a == b
    assert 0 <= a < STAGGER_MINUTES
    assert 0 <= c < STAGGER_MINUTES


def test_tier_is_mid_when_nothing_active():
    assert _hottest_tier_minutes([], datetime(2026, 1, 1, 12, tzinfo=UTC)) == MID_INTERVAL_MINUTES


def test_tier_is_mid_for_non_hot_statuses():
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    parcels = [
        {"status": "registered", "planned_from": None},
        {"status": "problem", "planned_from": None},
        {"status": "returning", "planned_from": None},
    ]
    assert _hottest_tier_minutes(parcels, now) == MID_INTERVAL_MINUTES


def test_tier_is_hot_when_out_for_delivery_without_planned_from():
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    parcels = [
        {"status": "in_transit", "planned_from": None},
        {"status": "out_for_delivery", "planned_from": None},
    ]
    assert _hottest_tier_minutes(parcels, now) == HOT_INTERVAL_MINUTES


def test_tier_is_hot_within_lookahead_of_planned_from():
    planned = datetime(2026, 1, 1, 13, 0, tzinfo=UTC)
    now = planned - timedelta(minutes=30)  # inside the 1h lookahead
    parcels = [{"status": "out_for_delivery", "planned_from": planned.isoformat()}]
    assert _hottest_tier_minutes(parcels, now) == HOT_INTERVAL_MINUTES


def test_tier_is_mid_before_lookahead_of_planned_from():
    planned = datetime(2026, 1, 1, 13, 0, tzinfo=UTC)
    now = planned - timedelta(hours=3)  # well outside the 1h lookahead
    parcels = [{"status": "out_for_delivery", "planned_from": planned.isoformat()}]
    assert _hottest_tier_minutes(parcels, now) == MID_INTERVAL_MINUTES


def test_daytime_candidate_outside_window_is_tier_plus_stagger():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    interval = _next_update_interval(now, MID_INTERVAL_MINUTES, "entry-1")
    stagger = _stagger_minutes("entry-1")
    assert interval == timedelta(minutes=MID_INTERVAL_MINUTES + stagger)


def test_now_inside_quiet_window_jumps_to_next_anchor():
    now = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)  # an anchor poll itself
    interval = _next_update_interval(now, HOT_INTERVAL_MINUTES, "entry-1")
    assert now + interval == datetime(2026, 1, 1, 6, 0, tzinfo=UTC)


def test_candidate_landing_in_quiet_window_clamps_to_the_midnight_anchor():
    now = datetime(2026, 1, 1, 23, 50, tzinfo=UTC)
    interval = _next_update_interval(now, MID_INTERVAL_MINUTES, "entry-1")
    assert now + interval == datetime(2026, 1, 2, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Dynamic polling — wired into DhlCoordinator._async_update_data
# ---------------------------------------------------------------------------


async def test_dhl_coordinator_auto_mode_recomputes_interval_and_never_stops(hass):
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value=[])

    coordinator = DhlCoordinator(
        hass, client, _mock_entry(refresh_interval=REFRESH_INTERVAL_AUTO)
    )
    await coordinator._async_update_data()

    assert coordinator.current_tier_minutes == MID_INTERVAL_MINUTES
    assert coordinator.update_interval is not None


async def test_dhl_coordinator_fixed_mode_keeps_configured_interval(hass):
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value=[])

    coordinator = DhlCoordinator(hass, client, _mock_entry(refresh_interval=60))
    await coordinator._async_update_data()

    assert coordinator.current_tier_minutes is None
    assert coordinator.update_interval == timedelta(minutes=60)


async def test_dhl_coordinator_auto_goes_hot_from_incoming_out_for_delivery(hass):
    """Incoming (not returning) out_for_delivery drives the tier hot."""
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value=[
        {
            "barcode": "IN1",
            "category": "IN_DELIVERY",
            "isReturn": False,
            "status": "OUT_FOR_DELIVERY",
            "receivingTimeIndication": None,
        },
    ])

    coordinator = DhlCoordinator(
        hass, client, _mock_entry(refresh_interval=REFRESH_INTERVAL_AUTO)
    )
    await coordinator._async_update_data()

    assert coordinator.current_tier_minutes == HOT_INTERVAL_MINUTES


async def test_dhl_coordinator_auto_goes_hot_from_returning_out_for_delivery(hass):
    """A returning (outgoing) parcel out_for_delivery also drives the tier hot —
    the hottest-status scan covers incoming AND outgoing (dynamic-polling.md
    Section 2.2 / Section 6), not just coordinator.data.
    """
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value=[
        # Incoming stays mid — nothing here should make the tier hot on its own.
        parcel_sample("IN_DELIVERY", barcode="incoming"),
        {
            "barcode": "RET1",
            "category": "UNDERWAY",
            "isReturn": True,
            "status": "OUT_FOR_DELIVERY",
            "receivingTimeIndication": None,
        },
    ])

    coordinator = DhlCoordinator(
        hass, client, _mock_entry(refresh_interval=REFRESH_INTERVAL_AUTO)
    )
    await coordinator._async_update_data()

    assert coordinator.current_tier_minutes == HOT_INTERVAL_MINUTES


# ---------------------------------------------------------------------------
# Dynamic polling — wired into DhlSentShipmentsCoordinator._async_update_data
# ---------------------------------------------------------------------------


async def test_sent_coordinator_auto_mode_recomputes_interval(hass):
    client = MagicMock()
    client.async_get_sent_shipments = AsyncMock(return_value=[])

    coordinator = DhlSentShipmentsCoordinator(
        hass, client, _mock_entry(refresh_interval=REFRESH_INTERVAL_AUTO)
    )
    await coordinator._async_update_data()

    assert coordinator.current_tier_minutes == MID_INTERVAL_MINUTES
    assert coordinator.update_interval is not None


async def test_sent_coordinator_fixed_mode_keeps_configured_interval(hass):
    client = MagicMock()
    client.async_get_sent_shipments = AsyncMock(return_value=[])

    coordinator = DhlSentShipmentsCoordinator(hass, client, _mock_entry(refresh_interval=60))
    await coordinator._async_update_data()

    assert coordinator.current_tier_minutes is None
    assert coordinator.update_interval == timedelta(minutes=60)


async def test_sent_coordinator_auto_goes_hot_from_out_for_delivery(hass):
    client = MagicMock()
    client.async_get_sent_shipments = AsyncMock(return_value=[
        {
            "barcode": "SENT1",
            "category": "IN_DELIVERY",
            "type": "outgoing",
            "status": "OUT_FOR_DELIVERY",
        },
    ])

    coordinator = DhlSentShipmentsCoordinator(
        hass, client, _mock_entry(refresh_interval=REFRESH_INTERVAL_AUTO)
    )
    await coordinator._async_update_data()

    assert coordinator.current_tier_minutes == HOT_INTERVAL_MINUTES
