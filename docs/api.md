# Hotel Booking API Reference

Download the OpenAPI schema from `GET /api/v1/schema/`. Use
`/api/v1/schema/?format=json` for JSON. To generate and validate the YAML schema
locally with the project environment configured, run:

```shell
python manage.py spectacular --file schema.yaml --validate --fail-on-warn
```

The development server defaults to `http://localhost:8000`. Use `Accept: application/json` for JSON responses and `Content-Type: application/json` when sending a JSON body. Only booking creation requires `Idempotency-Key`. Responses include `X-Request-ID` for log correlation. The product API accepts anonymous requests, and session-authenticated write requests are subject to CSRF validation.

## Room endpoints

The response shape for a single room is:

```json
{"id": 12, "label": "101", "beds": 2, "view": "sea"}
```

Room write fields are `label` (1–100 characters after trimming, case-insensitively unique), `beds` (integer from 1 to 5), and `view` (`sea`, `mountain`, or `yard`). Room writes normalize the view to lowercase and reject unknown or read-only body fields, including `id`.

| Method | Endpoint | Headers and body | Validation | Success | Endpoint-specific errors |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/rooms/` | Common headers, no body. | Optional exact filters `beds` and lowercase `view`. `page` is a positive integer (default 1) and `page_size` is 1–100 (default 20). | **200 OK**, paginated room list. | **400** invalid filter or pagination parameters, **404** page out of range. |
| POST | `/api/v1/rooms/` | Common JSON headers. Body `{"label":"101","beds":2,"view":"sea"}`. | All three writable fields are required. | **201 Created**, new room. | **400** invalid body, **409** duplicate label. |
| GET | `/api/v1/rooms/{id}/` | Common headers, no body. | `id` identifies an existing numeric room ID. | **200 OK**, room object. | **404** room missing or ID invalid. |
| PUT | `/api/v1/rooms/{id}/` | Common JSON headers. Body `{"label":"102","beds":3,"view":"yard"}`. | Existing room ID and all three writable fields. | **200 OK**, updated room. | **400** invalid body, **404** room missing or ID invalid, **409** duplicate label. |
| PATCH | `/api/v1/rooms/{id}/` | Common JSON headers. Example body `{"beds":3}`. | Existing room ID. Supplied fields follow the same rules as PUT. | **200 OK**, updated room. | **400** invalid body, **404** room missing or ID invalid, **409** duplicate label. |
| DELETE | `/api/v1/rooms/{id}/` | Common headers, no body. | Existing room ID. Optional query parameter `confirm` accepts `true` or `false`. Only `confirm=true` permits deleting a room that has bookings, and it also deletes those bookings. | **204 No Content**, empty body. | **400** invalid confirmation value, **404** room missing or ID invalid, **409** confirmation required. |

Example list response:

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [{"id": 12, "label": "101", "beds": 2, "view": "sea"}]
}
```

## Create a booking

| Field | Contract |
| --- | --- |
| Method and endpoint | `POST /api/v1/bookings/` |
| Headers | Common JSON headers and a required `Idempotency-Key` of 1–255 characters. Whitespace-only keys are invalid. |
| Request body | Required fields `user_id`, `beds`, `view`, `check_in`, and `check_out`. Unknown fields are rejected. |
| Validation | Send a nonblank `user_id` of at most 128 characters after trimming, a bed count of 1–5, and a lowercase supported view. Both timestamps must include an explicit UTC offset or `Z`. Checkout must be later than check-in. |
| Success | **201 Created** with the booking, UTC timestamps, and assigned room. A successful retry with the same key and equivalent payload returns the original 201 and body. |
| Errors | **400** invalid header, JSON, or field values. **409** no matching room is free, the key was used for different data, or the request is still processing. |

### Example request

```http
POST /api/v1/bookings/
Accept: application/json
Content-Type: application/json
Idempotency-Key: booking-demo-001
```

```json
{
  "user_id": "guest-42",
  "beds": 2,
  "view": "sea",
  "check_in": "2027-07-10T14:00:00+03:30",
  "check_out": "2027-07-12T11:00:00+03:30"
}
```

### Example 201 response

```json
{
  "id": 87,
  "user_id": "guest-42",
  "check_in": "2027-07-10T10:30:00Z",
  "check_out": "2027-07-12T07:30:00Z",
  "created_at": "2027-07-01T09:15:00Z",
  "room": {"id": 12, "label": "101", "beds": 2, "view": "sea"}
}
```

## Discovery and health

| Method | Endpoint | Headers and body | Validation | Success | Errors |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/` | Common headers, no body. | No input fields. | **200 OK**, DRF API root with a link to the rooms collection. | Common HTTP errors below. |
| GET | `/health/` | No required headers or body. | No input fields. | **200 OK**, `{"status":"ok"}`. This endpoint checks that the application responds. | General Django HTTP errors apply. |

## Error responses

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed.",
    "fields": {"beds": ["Ensure this value is greater than or equal to 1."]}
  }
}
```

| Status | Error codes and meaning |
| --- | --- |
| 400 Bad Request | `validation_error`. Field-level errors can include `error.fields`. |
| 403 Forbidden | `permission_denied`, for example when a session-authenticated write fails CSRF validation. |
| 404 Not Found | `room_not_found`, `page_not_found`, or `not_found` for an unmatched API URL with `DEBUG=False`. |
| 405 Method Not Allowed | `method_not_allowed` for unsupported methods on DRF endpoints. |
| 406 Not Acceptable | `not_acceptable` when DRF cannot satisfy the requested response media type. |
| 409 Conflict | `duplicate_room_label`, `confirmation_required`, `no_available_room`, `idempotency_key_reused`, or `idempotency_request_in_progress`. |
| 415 Unsupported Media Type | `unsupported_media_type` when an endpoint tries to parse a body with an unsupported media type. |
| 500 Internal Server Error | `internal_server_error` with a generic message when `DEBUG=False`. |

See [architecture](architecture.md) for allocation, concurrency, and retry behavior.
