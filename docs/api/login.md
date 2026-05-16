# POST /api/user/login

Authenticates the user and establishes a session. Must be called before any other endpoint.

## Request

**URL:** `https://my.dhlecommerce.nl/api/user/login`  
**Method:** `POST`  
**Content-Type:** `application/json`

### Body

```json
{
  "email": "user@example.com",
  "password": "secret"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `email` | string | yes | DHL eCommerce NL account email address |
| `password` | string | yes | DHL eCommerce NL account password |

## Response

**Status:** `200 OK` on success. Any other status indicates a failed login.

### Cookies set on success

| Cookie | Description |
|--------|-------------|
| `X-AUTH-TOKEN` | Session token. Sent automatically by the cookie jar on subsequent requests. |
| `XSRF-TOKEN` | CSRF token. Must also be sent as the `x-xsrf-token` request header on subsequent requests. |

### Body

```json
{
  "userId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "email": "user@example.com",
  "firstName": "Jane",
  "lastName": "Doe",
  "locale": "nl_NL"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `userId` | string (UUID) | Unique identifier for the account. Used as the base for all sensor unique IDs in the integration. |
| `email` | string | The authenticated email address. Used as the device name in Home Assistant. |
| `firstName` | string | Account holder first name. |
| `lastName` | string | Account holder last name. |
| `locale` | string | Account locale, e.g. `nl_NL`. |

## Error handling

| Status | Meaning |
|--------|---------|
| `200` | Login successful |
| `401` | Invalid credentials |
| `4xx/5xx` | Other failure — the integration raises `DhlAuthError` with the status code |

## Notes

- The integration re-uses the HA-managed `aiohttp` session so cookies persist across all subsequent API calls without manual handling.
- On `401`/`403` responses from other endpoints, the integration calls this endpoint once to re-establish the session before retrying.
