# DHL eCommerce NL API Reference

This directory contains reference documentation for the DHL eCommerce NL API endpoints used by this integration. Each file documents one endpoint with its URL, authentication requirements, request format, and an annotated example response.

## Endpoints

| File | Endpoint | Description |
|------|----------|-------------|
| [login.md](login.md) | `POST /api/user/login` | Authenticate and obtain session cookies |
| [parcels.md](parcels.md) | `GET /receiver-parcel-api/parcels` | Incoming parcels on their way to you |
| [sent_shipments.md](sent_shipments.md) | `GET /api/orders/sentShipments` | Outgoing packages you have sent |

## Base URL

```
https://my.dhlecommerce.nl
```

## Authentication

All endpoints (except login) require two cookies set by the login response:

- `X-AUTH-TOKEN` — session token, set automatically in the cookie jar
- `XSRF-TOKEN` — CSRF token, must also be sent as the `x-xsrf-token` request header

The integration uses the `aiohttp` session cookie jar to persist these automatically. The XSRF token is extracted from the jar and added as a header on each request.
