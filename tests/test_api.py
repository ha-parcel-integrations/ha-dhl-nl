"""Unit tests for the DHL API client."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.dhl_nl.api import (
    DhlApiClient,
    DhlApiError,
    DhlAuthError,
)
from custom_components.dhl_nl.const import COOKIE_XSRF, HEADER_XSRF


def _mock_response(*, status: int = 200, json_payload=None) -> AsyncMock:
    """Build an aiohttp.ClientResponse stand-in for use as an async context manager."""
    response = MagicMock()
    response.status = status
    response.json = AsyncMock(return_value=json_payload if json_payload is not None else {})
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


def _mock_session(*, posts=None, gets=None) -> MagicMock:
    """Build a session whose ``post`` and ``get`` walk through the provided responses."""
    session = MagicMock()
    session.cookie_jar = []
    if posts is not None:
        session.post = MagicMock(side_effect=posts)
    else:
        session.post = MagicMock(return_value=_mock_response())
    if gets is not None:
        session.get = MagicMock(side_effect=gets)
    else:
        session.get = MagicMock(return_value=_mock_response())
    return session


# ---------------------------------------------------------------------------
# async_login
# ---------------------------------------------------------------------------


async def test_async_login_returns_userinfo_and_caches_it():
    session = _mock_session(
        posts=[_mock_response(json_payload={"userId": "u1", "email": "a@b"})]
    )
    client = DhlApiClient("a@b", "pw", session)
    result = await client.async_login()
    assert result == {"userId": "u1", "email": "a@b"}
    assert client.user_info == {"userId": "u1", "email": "a@b"}


async def test_async_login_raises_dhl_auth_error_on_401():
    session = _mock_session(posts=[_mock_response(status=401)])
    client = DhlApiClient("a@b", "pw", session)
    with pytest.raises(DhlAuthError) as err:
        await client.async_login()
    assert err.value.status_code == 401


async def test_async_login_raises_dhl_api_error_on_server_error():
    """A 5xx on login is a transient outage, not an auth failure."""
    session = _mock_session(posts=[_mock_response(status=503)])
    client = DhlApiClient("a@b", "pw", session)
    with pytest.raises(DhlApiError) as err:
        await client.async_login()
    assert err.value.status_code == 503


async def test_user_info_is_none_before_login():
    session = _mock_session()
    client = DhlApiClient("a@b", "pw", session)
    assert client.user_info is None


# ---------------------------------------------------------------------------
# _xsrf_headers
# ---------------------------------------------------------------------------


def test_xsrf_headers_returns_empty_when_no_cookie():
    session = _mock_session()
    client = DhlApiClient("a@b", "pw", session)
    assert client._xsrf_headers() == {}


def test_xsrf_headers_returns_header_when_cookie_present():
    cookie = MagicMock()
    cookie.key = COOKIE_XSRF
    cookie.value = "xyz"
    session = _mock_session()
    session.cookie_jar = [cookie]
    client = DhlApiClient("a@b", "pw", session)
    assert client._xsrf_headers() == {HEADER_XSRF: "xyz"}


# ---------------------------------------------------------------------------
# async_get_parcels
# ---------------------------------------------------------------------------


async def test_async_get_parcels_returns_parcel_list():
    payload = {"parcels": [{"barcode": "ABC"}, {"barcode": "DEF"}]}
    session = _mock_session(gets=[_mock_response(json_payload=payload)])
    client = DhlApiClient("a@b", "pw", session)
    result = await client.async_get_parcels()
    assert result == payload["parcels"]


async def test_async_get_parcels_returns_empty_when_payload_missing_key():
    session = _mock_session(gets=[_mock_response(json_payload={})])
    client = DhlApiClient("a@b", "pw", session)
    assert await client.async_get_parcels() == []


async def test_async_get_parcels_propagates_non_auth_api_error():
    session = _mock_session(gets=[_mock_response(status=500)])
    client = DhlApiClient("a@b", "pw", session)
    with pytest.raises(DhlApiError) as err:
        await client.async_get_parcels()
    assert err.value.status_code == 500


async def test_async_get_parcels_reauths_once_on_401():
    """A 401 from the parcels endpoint triggers a single re-login and a retry."""
    first = _mock_response(status=401)
    login = _mock_response(json_payload={"userId": "u1"})
    retry = _mock_response(json_payload={"parcels": [{"barcode": "X"}]})

    session = _mock_session(gets=[first, retry], posts=[login])
    client = DhlApiClient("a@b", "pw", session)
    result = await client.async_get_parcels()
    assert result == [{"barcode": "X"}]
    # The login endpoint was hit exactly once during the recovery
    session.post.assert_called_once()


async def test_async_get_parcels_reauths_once_on_403():
    first = _mock_response(status=403)
    login = _mock_response(json_payload={"userId": "u1"})
    retry = _mock_response(json_payload={"parcels": []})

    session = _mock_session(gets=[first, retry], posts=[login])
    client = DhlApiClient("a@b", "pw", session)
    assert await client.async_get_parcels() == []


# ---------------------------------------------------------------------------
# async_get_sent_shipments
# ---------------------------------------------------------------------------


async def test_async_get_sent_shipments_returns_list():
    payload = [{"barcode": "S1"}, {"barcode": "S2"}]
    session = _mock_session(gets=[_mock_response(json_payload=payload)])
    client = DhlApiClient("a@b", "pw", session)
    assert await client.async_get_sent_shipments() == payload


async def test_async_get_sent_shipments_returns_empty_for_non_list_payload():
    """Some DHL accounts respond with a dict — treat that as empty."""
    session = _mock_session(gets=[_mock_response(json_payload={"error": "huh"})])
    client = DhlApiClient("a@b", "pw", session)
    assert await client.async_get_sent_shipments() == []


async def test_async_get_sent_shipments_propagates_api_error():
    session = _mock_session(gets=[_mock_response(status=500)])
    client = DhlApiClient("a@b", "pw", session)
    with pytest.raises(DhlApiError):
        await client.async_get_sent_shipments()


async def test_async_get_sent_shipments_reauths_once_on_401():
    first = _mock_response(status=401)
    login = _mock_response(json_payload={"userId": "u1"})
    retry = _mock_response(json_payload=[{"barcode": "S"}])

    session = _mock_session(gets=[first, retry], posts=[login])
    client = DhlApiClient("a@b", "pw", session)
    assert await client.async_get_sent_shipments() == [{"barcode": "S"}]


# ---------------------------------------------------------------------------
# Exception representations
# ---------------------------------------------------------------------------


def test_dhl_auth_error_carries_status_code_in_message():
    err = DhlAuthError(401)
    assert "401" in str(err)
    assert err.status_code == 401


def test_dhl_api_error_carries_status_code_in_message():
    err = DhlApiError(503)
    assert "503" in str(err)
    assert err.status_code == 503


# ---------------------------------------------------------------------------
# async_get_track_trace
# ---------------------------------------------------------------------------


def _mock_text_response(*, status: int = 200, text: str = "") -> MagicMock:
    """Like _mock_response but exposes ``.text()`` for the text/plain endpoint."""
    response = MagicMock()
    response.status = status
    response.text = AsyncMock(return_value=text)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


async def test_async_get_track_trace_parses_text_plain_body_and_params():
    import json as _json

    payload = [{"id": "u", "view": {"phases": []}}]
    session = _mock_session(gets=[_mock_text_response(text=_json.dumps(payload))])
    client = DhlApiClient("a@b", "pw", session)

    result = await client.async_get_track_trace("JX1", "1234AB", "u")

    assert result == payload
    kwargs = session.get.call_args.kwargs
    assert kwargs["params"]["key"] == "JX1+1234AB"
    assert kwargs["params"]["uuid"] == "u"
    assert kwargs["params"]["role"] == "consumer-receiver"


async def test_async_get_track_trace_returns_none_on_non_200():
    session = _mock_session(gets=[_mock_text_response(status=500, text="")])
    client = DhlApiClient("a@b", "pw", session)
    assert await client.async_get_track_trace("JX1", "1234AB", "u") is None


async def test_async_get_track_trace_returns_none_on_malformed_body():
    session = _mock_session(gets=[_mock_text_response(text="not json at all")])
    client = DhlApiClient("a@b", "pw", session)
    assert await client.async_get_track_trace("JX1", "1234AB", "u") is None
