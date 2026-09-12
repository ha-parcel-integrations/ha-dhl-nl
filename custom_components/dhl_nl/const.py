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
    UNKNOWN = "unknown"                     # Raw status we have not mapped yet — logged at warning level

PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

# Every optional key the parcel contract defines. CAPABILITIES below must be a
# subset of this — it exists so a typo in CAPABILITIES fails a test instead of
# silently dropping this carrier off a table on the docs site.
KNOWN_CAPABILITIES = frozenset(
    {"weight", "dimensions", "delivery_window", "pickup_point", "url", "history"}
)

# Which optional contract fields this carrier's API actually populates — feeds
# the comparison table on the docs site. Keep in lockstep with
# normalize_parcel() in parcels.py: everything not listed here comes back as a
# literal None there. DHL NL never exposes weight or package dimensions
# through receiver-parcel-api.
CAPABILITIES = frozenset({"delivery_window", "pickup_point", "url", "history"})

LOGIN_URL = "https://my.dhlecommerce.nl/api/user/login"
PARCELS_URL = "https://my.dhlecommerce.nl/receiver-parcel-api/parcels"
SENT_SHIPMENTS_URL = "https://my.dhlecommerce.nl/api/orders/sentShipments?max=250"
# Per-parcel track-and-trace timeline. Backs the phase/event history used by
# the opt-in history feature. Returns a JSON array with a ``text/plain``
# mimetype, so the client parses the body with ``json.loads`` rather than
# ``response.json()``.
TRACK_TRACE_URL = "https://my.dhlecommerce.nl/receiver-parcel-api/track-trace"
TRACK_TRACE_ROLE = "consumer-receiver"

CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Dynamic, status-driven polling — unconditional, no user-facing interval
# option. DHL NL tracks both incoming parcels and outgoing (returns + sent)
# parcels, so the hottest-status scan runs over both directions — see
# coordinator.py.
#
# Quiet window: no polling between these local hours except the two anchors
# below, for overnight / end-of-day catch-up.
QUIET_WINDOW_START_HOUR = 0
QUIET_WINDOW_END_HOUR = 6

# Cadence while polling is active (minutes). Hot = at least one active
# incoming or outgoing parcel is out_for_delivery within HOT_LOOKAHEAD_HOURS
# of its planned_from (or has no planned_from at all); mid = anything else
# still in flight, or nothing tracked at all. This is an account-based
# coordinator, so it never fully stops —
# the mid-tier poll is also how a new shipment gets discovered, since a
# single account call is the only way to see one that appeared without going
# through this integration.
HOT_INTERVAL_MINUTES = 15
MID_INTERVAL_MINUTES = 45
HOT_LOOKAHEAD_HOURS = 1

# Small, stable per-install offset added to every computed interval so
# different installs don't all hit an anchor or tier boundary at the same
# second. Deterministic (hash of the config entry id), not random.
STAGGER_MINUTES = 7

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
