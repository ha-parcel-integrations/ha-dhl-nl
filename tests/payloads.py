"""Sample DHL API payloads shared by the test modules.

These are the **raw** shapes the API returns — what ``normalize_parcel`` and the
coordinator consume. Keep them here rather than inline in each test module:
when the payload shape turns out to differ from what we assumed, there is then
exactly one place to fix.

Every helper returns a **fresh** dict, so a test may mutate what it gets back
without leaking into the next one.
"""
from __future__ import annotations

ACTIVE_BARCODE = "TEST123"
DELIVERED_BARCODE = "DEL123"
RETURN_BARCODE = "RET123"
SENT_BARCODE = "SENT123"
HISTORY_BARCODE = "JX1"


def parcel_sample(
    category: str,
    is_return: bool = False,
    moment: str | None = None,
    barcode: str = ACTIVE_BARCODE,
) -> dict:
    """An incoming parcel, as the parcels endpoint lists it."""
    indication = (
        {"indicationType": "MomentIndication", "moment": moment} if moment else None
    )
    return {
        "barcode": barcode,
        "category": category,
        "isReturn": is_return,
        "receivingTimeIndication": indication,
    }


def detail_sample(
    barcode: str = ACTIVE_BARCODE,
    status: str = "IN_DELIVERY",
    location_type: str = "ADDRESS",
    indication: dict | None = None,
    category: str = "IN_DELIVERY",
) -> dict:
    """A parcel carrying the destination/sender blocks the sensors read."""
    return {
        "barcode": barcode,
        "status": status,
        "category": category,
        "destination": {"locationType": location_type, "name": "DHL ServicePoint"},
        "sender": {"name": "Example Sender"},
        "receivingTimeIndication": indication,
    }


def delivered_sample(
    barcode: str = DELIVERED_BARCODE,
    sender_name: str | None = "Test Sender",
) -> dict:
    """A delivered parcel with a moment indication.

    ``sender_name=None`` reproduces the real case where DHL omits the block.
    """
    return {
        "barcode": barcode,
        "category": "DELIVERED",
        "isReturn": False,
        "status": "DELIVERED",
        "sender": {"name": sender_name} if sender_name else None,
        "receivingTimeIndication": {
            "indicationType": "MomentIndication",
            "moment": "2026-05-30T14:00:00Z",
        },
    }


def return_sample(barcode: str = RETURN_BARCODE, category: str = "UNDERWAY") -> dict:
    """A return the user ships back — an incoming parcel with ``isReturn``."""
    return {
        "barcode": barcode,
        "category": category,
        "isReturn": True,
        "status": "PARCEL_READY_FOR_RETURN_TO_HUB",
        "sender": {"name": "Test User"},
        "receiver": {"name": "AE-RTN-NL"},
    }


def sent_shipment_sample(barcode: str = SENT_BARCODE) -> dict:
    """A shipment from the separate sent-shipments endpoint."""
    return {
        "barcode": barcode,
        "category": "IN_DELIVERY",
        "sender": {"name": "Test User"},
    }


def shipment_sample(category: str, shipment_type: str = "outgoing") -> dict:
    """The minimal sent-shipment shape the active-filter keys on."""
    return {"barcode": SENT_BARCODE, "category": category, "type": shipment_type}


def history_parcel_sample(
    barcode: str = HISTORY_BARCODE,
    status: str = "OUT_FOR_DELIVERY",
) -> dict:
    """A parcel with the fields the track & trace enrichment needs."""
    return {
        "barcode": barcode,
        "parcelId": "uuid-1",
        "status": status,
        "category": "IN_DELIVERY",
        "receiver": {"address": {"postalCode": "1234 AB"}},
    }


def track_trace() -> list[dict]:
    """A track & trace response — DHL returns its phases newest-first."""
    return [
        {
            "id": "uuid-1",
            "barcode": HISTORY_BARCODE,
            "view": {
                "phases": [
                    {"phase": "DELIVERED", "events": [
                        {"timestamp": "2026-06-24T17:23:13Z", "key": "DELIVERED", "exception": False},
                    ]},
                    {"phase": "IN_DELIVERY", "events": [
                        {"timestamp": "2026-06-24T15:17:49Z", "key": "OUT_FOR_DELIVERY", "exception": False},
                    ]},
                    {"phase": "UNDERWAY", "events": [
                        {"timestamp": "2026-06-24T12:18:34Z", "key": "PARCEL_ARRIVED_AT_LOCAL_DEPOT", "exception": False},
                        {"timestamp": "2026-06-24T02:00:00Z", "key": "PARCEL_SORTED_AT_HUB", "exception": False},
                    ]},
                    {"phase": "DATA_RECEIVED", "events": [
                        {"timestamp": "2026-06-23T11:05:01Z", "key": "PRENOTIFICATION_RECEIVED", "exception": False},
                    ]},
                ],
            },
        }
    ]
