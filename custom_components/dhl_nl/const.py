"""Constants for the DHL Package Tracker integration."""
from homeassistant.const import Platform

DOMAIN = "dhl_nl"

PLATFORMS = [Platform.SENSOR]

LOGIN_URL = "https://my.dhlecommerce.nl/api/user/login"
PARCELS_URL = "https://my.dhlecommerce.nl/receiver-parcel-api/parcels"

POLL_INTERVAL = 1800  # seconds (30 minutes)

ACTIVE_CATEGORIES = frozenset({
    "PROBLEM",
    "CUSTOMS",
    "DATA_RECEIVED",
    "EXCEPTION",
    "INTERVENTION",
    "IN_DELIVERY",
    "LEG",
    "UNDERWAY",
    "UNKNOWN",
})

COOKIE_AUTH = "X-AUTH-TOKEN"
COOKIE_XSRF = "XSRF-TOKEN"
HEADER_XSRF = "x-xsrf-token"
