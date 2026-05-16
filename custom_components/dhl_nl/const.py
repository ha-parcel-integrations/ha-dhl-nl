"""Constants for the DHL Package Tracker integration."""
from homeassistant.const import Platform

DOMAIN = "dhl_nl"

PLATFORMS = [Platform.SENSOR]

LOGIN_URL = "https://my.dhlecommerce.nl/api/user/login"
PARCELS_URL = "https://my.dhlecommerce.nl/receiver-parcel-api/parcels"
SENT_SHIPMENTS_URL = "https://my.dhlecommerce.nl/api/orders/sentShipments?max=250"

POLL_INTERVAL = 1800  # seconds (30 minutes)

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

COOKIE_AUTH = "X-AUTH-TOKEN"
COOKIE_XSRF = "XSRF-TOKEN"
HEADER_XSRF = "x-xsrf-token"
