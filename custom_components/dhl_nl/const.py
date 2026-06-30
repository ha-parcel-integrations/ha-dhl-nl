"""Constants for the DHL Package Tracker integration."""
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "dhl_nl"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    Maps the carrier-specific raw status strings into a small set of
    canonical values shared across DHL, DPD, PostNL and the parcel
    aggregator. Listed in roughly the order a parcel moves through.
    """

    REGISTERED = "registered"               # Sender announced the parcel; carrier has not handed-over yet
    IN_TRANSIT = "in_transit"               # In the carrier's network, somewhere between sender and delivery point
    OUT_FOR_DELIVERY = "out_for_delivery"   # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"     # Arrived at the chosen ServicePoint / PostNL Point / ParcelShop
    DELIVERED = "delivered"                 # Handed over (mailbox, recipient, neighbour, picked up)
    RETURNING = "returning"                 # Failed delivery, going back to sender
    PROBLEM = "problem"                     # Carrier reports an exception, intervention, or other issue
    UNKNOWN = "unknown"                     # Raw status we have not mapped yet — logged at info level

PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

LOGIN_URL = "https://my.dhlecommerce.nl/api/user/login"
PARCELS_URL = "https://my.dhlecommerce.nl/receiver-parcel-api/parcels"
SENT_SHIPMENTS_URL = "https://my.dhlecommerce.nl/api/orders/sentShipments?max=250"
# Per-parcel track-and-trace timeline. Backs the phase/event history used by
# the opt-in history feature. Returns a JSON array with a ``text/plain``
# mimetype, so the client parses the body with ``json.loads`` rather than
# ``response.json()``.
TRACK_TRACE_URL = "https://my.dhlecommerce.nl/receiver-parcel-api/track-trace"
TRACK_TRACE_ROLE = "consumer-receiver"

POLL_INTERVAL = 900  # seconds (15 minutes) — legacy hard-coded fallback

CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Refresh interval (minutes) controls how often the coordinator polls DHL.
# Default 30 min keeps the load on the consumer API gentle; the minimum is
# 15 min for the same reason (parcel status rarely changes faster). Maximum
# 240 min (4h) is the "I just want one or two checks a day" knob.
CONF_REFRESH_INTERVAL = "refresh_interval"
REFRESH_INTERVAL_OPTIONS = (15, 30, 60, 120, 240)
DEFAULT_REFRESH_INTERVAL = 30

CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False
# Cap each parcel's history to the most recent N events so the attribute
# stays well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20

# All categories that indicate a shipment is still active (not yet delivered).
# Applies to both incoming parcels and outgoing sent shipments.
# DELIVERED is the only terminal category and is intentionally excluded.
ACTIVE_CATEGORIES = frozenset({
    "CUSTOMS",        # Being processed by customs
    "DATA_RECEIVED",  # Shipment registered / label created
    "EXCEPTION",      # Something went wrong, delay expected
    "IN_DELIVERY",    # Parcel is in transit
    "INTERVENTION",   # An intervention occurred in the delivery process
    "LEG",            # Domestic leg registered (early trace event)
    "PROBLEM",        # Same as EXCEPTION
    "UNDERWAY",       # Parcel is being sorted
    "UNKNOWN",        # Status unknown
})

STATUS_AT_SERVICE_POINT = "NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT"
STATUS_COLLECTED_AT_SERVICE_POINT = "COLLECTED_AT_PARCELSHOP"

COOKIE_AUTH = "X-AUTH-TOKEN"
COOKIE_XSRF = "XSRF-TOKEN"
HEADER_XSRF = "x-xsrf-token"
