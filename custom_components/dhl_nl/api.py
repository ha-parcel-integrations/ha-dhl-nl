"""DHL eCommerce NL API client."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import COOKIE_XSRF, HEADER_XSRF, LOGIN_URL, PARCELS_URL

_LOGGER = logging.getLogger(__name__)


class DhlAuthError(Exception):
    """Raised when the DHL login endpoint returns a non-200 status."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"DHL authentication failed with status {status_code}")
        self.status_code = status_code


class DhlApiError(Exception):
    """Raised when a DHL API call returns a non-200 status."""

    def __init__(self, status_code: int) -> None:
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
            session: Shared aiohttp session whose cookie jar persists cookies
                     between requests.
        """
        self._email = email
        self._password = password
        self._session = session
        self._user_info: dict[str, Any] | None = None

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
            DhlAuthError: If the server returns any status other than 200.
            aiohttp.ClientError: On network-level failures.
        """
        payload = {"email": self._email, "password": self._password}
        headers = {"Content-Type": "application/json"}

        async with self._session.post(
            LOGIN_URL, json=payload, headers=headers
        ) as response:
            if response.status != 200:
                raise DhlAuthError(response.status)

            data: dict[str, Any] = await response.json()

        self._user_info = data
        return data

    async def async_get_parcels(self) -> list[dict[str, Any]]:
        """Retrieve the parcel list from the DHL parcels endpoint.

        Uses the cookies stored in the session's cookie jar (set by a prior
        call to :meth:`async_login`) and adds the ``x-xsrf-token`` request
        header whose value is read from the ``XSRF-TOKEN`` cookie.

        Raises:
            DhlApiError: If the server returns any status other than 200.
            aiohttp.ClientError: On network-level failures.
        """
        xsrf_token = self._get_xsrf_token()
        headers = {HEADER_XSRF: xsrf_token} if xsrf_token else {}

        async with self._session.get(PARCELS_URL, headers=headers) as response:
            if response.status != 200:
                raise DhlApiError(response.status)

            data: dict[str, Any] = await response.json()

        return data.get("parcels", [])

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

    def _get_xsrf_token(self) -> str | None:
        """Extract the XSRF-TOKEN value from the session's cookie jar."""
        cookie_jar = self._session.cookie_jar
        for cookie in cookie_jar:
            if cookie.key == COOKIE_XSRF:
                return cookie.value
        return None
