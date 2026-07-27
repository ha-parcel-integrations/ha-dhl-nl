"""DHL eCommerce NL API client."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

from .const import (
    COOKIE_XSRF,
    HEADER_XSRF,
    LOGIN_URL,
    PARCELS_URL,
    SENT_SHIPMENTS_URL,
    TRACK_TRACE_ROLE,
    TRACK_TRACE_URL,
)

_LOGGER = logging.getLogger(__name__)


class DhlAuthError(Exception):
    """Raised when the DHL login endpoint returns a non-200 status."""

    def __init__(self, status_code: int) -> None:
        """Store the HTTP status code that triggered the auth error."""
        super().__init__(f"DHL authentication failed with status {status_code}")
        self.status_code = status_code


class DhlApiError(Exception):
    """Raised when a DHL API call returns a non-200 status."""

    def __init__(self, status_code: int) -> None:
        """Store the HTTP status code that triggered the API error."""
        super().__init__(f"DHL API request failed with status {status_code}")
        self.status_code = status_code


class DhlApiClient:
    """Client for the DHL eCommerce NL API."""

    def __init__(
        self,
        email: str,
        password: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialise the client.

        Args:
            email: DHL account e-mail address.
            password: DHL account password.
            session: Dedicated aiohttp session with an isolated cookie jar,
                     ensuring multiple DHL accounts do not share auth cookies.

        """
        self._email = email
        self._password = password
        self._session = session
        self._user_info: dict[str, Any] | None = None
        self._reauth_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def async_login(self) -> dict[str, Any]:
        """Authenticate against the DHL login endpoint.

        POSTs ``{"email": ..., "password": ...}`` to ``LOGIN_URL`` with
        ``Content-Type: application/json``.  On success the session's cookie
        jar will contain the ``X-AUTH-TOKEN`` and ``XSRF-TOKEN`` cookies and
        the user-info dict from the response body is returned and cached.

        Raises:
            DhlAuthError: If the server rejects the credentials (401/403).
            DhlApiError: On any other non-200 status (e.g. a 5xx outage) —
                distinct from an auth failure so callers never push the user
                into reauth over a transient DHL hiccup.
            aiohttp.ClientError: On network-level failures.

        """
        payload = {"email": self._email, "password": self._password}
        headers = {"Content-Type": "application/json"}

        async with self._session.post(
            LOGIN_URL, json=payload, headers=headers
        ) as response:
            if response.status in (401, 403):
                raise DhlAuthError(response.status)
            if response.status != 200:
                raise DhlApiError(response.status)

            data: dict[str, Any] = await response.json()

        self._user_info = data
        return data

    async def async_get_parcels(self) -> list[dict[str, Any]]:
        """Retrieve the parcel list, re-authenticating once on session expiry."""
        async def _fetch() -> list[dict[str, Any]]:
            headers = self._xsrf_headers()
            async with self._session.get(PARCELS_URL, headers=headers) as response:
                if response.status != 200:
                    raise DhlApiError(response.status)
                data: dict[str, Any] = await response.json()
            return data.get("parcels", [])

        return await self._async_call_with_reauth(_fetch)

    async def async_get_sent_shipments(self) -> list[dict[str, Any]]:
        """Retrieve the sent shipments list, re-authenticating once on session expiry."""
        async def _fetch() -> list[dict[str, Any]]:
            headers = self._xsrf_headers()
            async with self._session.get(SENT_SHIPMENTS_URL, headers=headers) as response:
                if response.status != 200:
                    raise DhlApiError(response.status)
                data: list[dict[str, Any]] = await response.json()
            return data if isinstance(data, list) else []

        return await self._async_call_with_reauth(_fetch)

    async def async_get_track_trace(
        self, barcode: str, postal_code: str, parcel_id: str
    ) -> list[dict[str, Any]] | None:
        """Fetch a parcel's track-and-trace timeline. Best-effort: ``None`` on failure.

        Backs the opt-in history feature. The endpoint authenticates with the
        same cookies as the parcels call, but returns its JSON body with a
        ``text/plain`` mimetype — so the body is parsed with ``json.loads``
        rather than ``response.json()``. ``yarl`` URL-encodes the ``+`` in the
        ``key`` parameter to ``%2B`` as the endpoint expects. Any failure
        (non-200, network error, malformed body) yields ``None`` so a broken
        history call never breaks the main parcels poll.
        """
        params = {
            "key": f"{barcode}+{postal_code}",
            "role": TRACK_TRACE_ROLE,
            "uuid": parcel_id,
        }

        async def _fetch() -> list[dict[str, Any]]:
            headers = self._xsrf_headers()
            async with self._session.get(
                TRACK_TRACE_URL, params=params, headers=headers
            ) as response:
                if response.status != 200:
                    raise DhlApiError(response.status)
                text = await response.text()
            return json.loads(text)

        try:
            return await self._async_call_with_reauth(_fetch)
        except (DhlApiError, aiohttp.ClientError, ValueError):
            return None

    @property
    def user_info(self) -> dict[str, Any] | None:
        """Return the user-info dict from the last successful login.

        Returns ``None`` if :meth:`async_login` has not yet been called
        successfully.
        """
        return self._user_info

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _async_call_with_reauth(self, coro_fn: Any) -> Any:
        """Call coro_fn(), re-authenticating once if the session has expired.

        A lock prevents concurrent re-login attempts when multiple coordinators
        share this client instance and both hit a 401/403 at the same time.
        """
        try:
            return await coro_fn()
        except DhlApiError as err:
            if err.status_code not in (401, 403):
                raise
        async with self._reauth_lock:
            await self.async_login()
        return await coro_fn()

    def _xsrf_headers(self) -> dict[str, str]:
        """Return the x-xsrf-token header dict, or empty dict if no token is present."""
        for cookie in self._session.cookie_jar:
            if cookie.key == COOKIE_XSRF:
                return {HEADER_XSRF: cookie.value}
        return {}
