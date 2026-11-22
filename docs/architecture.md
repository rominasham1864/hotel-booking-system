# Hotel Booking System Architecture

The system is a Django modular monolith backed by PostgreSQL. This document explains the internal design. See the [API reference](api.md) for HTTP contracts.

## Application boundaries

| App | Responsibilities |
| --- | --- |
| `rooms` | Room inventory, case-insensitive label uniqueness, CRUD, list filters, pagination, and confirmed deletion. |
| `bookings` | Reservations, feature matching, availability checks, room allocation, and idempotent booking creation. |
| `config` | Settings, URLs, API error formatting, request correlation, and JSON logging. |

| Layer | Responsibilities |
| --- | --- |
| Models | Define the persisted data, relationships, indexes, and database constraints. |
| Serializers | Validate request fields and format responses. Booking timestamps become timezone-aware UTC values before allocation. |
| Views | Handle HTTP input and headers, call the booking service, and translate domain failures into HTTP responses. Room CRUD uses a DRF viewset with transactional database writes. |
| Services | `bookings.services` validates domain inputs and allocates an available room. `bookings.idempotency` coordinates retries and persists the response snapshot. |

## Database structure

| Table | Fields and invariants |
| --- | --- |
| `rooms_room` | `id`, `label`, `beds`, `view`. Labels are trimmed, nonempty, and unique under `LOWER(label)`. Bed counts are limited to 1–5 and views to the supported choices. An index on `(beds, view)` supports feature matching. |
| `bookings_booking` | `id`, `room_id`, `user_id`, `check_in`, `check_out`, `created_at`. The user reference is trimmed and nonempty. Checkout must follow check-in. An index on `(room_id, check_in, check_out)` supports availability lookups. |
| `bookings_idempotencyrecord` | `id`, `user_id`, `key`, `request_id`, `request_hash`, `status`, `booking_id`, `response_status`, `response_body`, and creation/update timestamps. The pair `(user_id, key)` and the request UUID are unique. A processing record has empty result fields. A completed record requires a booking ID, HTTP status 201, and a JSON response body. |

Each booking belongs to one room. A room can have many bookings in distinct time ranges. The Django foreign key uses `PROTECT` to prevent ordinary deletion of rooms with bookings. The confirmed deletion endpoint locks the room and deletes its bookings and then the room in one transaction. The idempotency record stores the booking ID as a scalar so the snapshot remains replayable after confirmed deletion.

## Transaction management and concurrency

The booking API starts an outer transaction for the idempotency record, booking, and response snapshot. The allocator then:

1. Reads candidate room IDs with an exact bed count and view match, ordered by ascending ID.
2. Locks a candidate with `SELECT ... FOR UPDATE` and rechecks its features and availability. Under PostgreSQL's default `READ COMMITTED` isolation, this sees committed reservations written while the request waited for the room lock.
3. Creates the booking in a nested atomic block. If the exclusion constraint rejects the insert, the savepoint rolls back and the service tries the next candidate. Unrelated integrity errors propagate.

The booking and response snapshot commit together. Unavailability and unexpected failures, including response serialization failure, roll back the pending idempotency record and any booking write.

### PostgreSQL exclusion constraint

The [overlap migration](../bookings/migrations/0002_booking_no_overlap.py) installs `btree_gist` and creates `bookings_no_overlap`:

```sql
EXCLUDE USING gist (
    room_id WITH =,
    tstzrange(check_in, check_out, '[)') WITH &&
)
```

The constraint rejects overlapping ranges for the same room, including inserts and updates that bypass the allocator. The half-open interval `[check_in, check_out)` allows a stay to begin at the previous stay's checkout. The application uses the equivalent predicates `existing.check_in < requested.check_out` and `existing.check_out > requested.check_in`.

## Idempotency

The service fingerprints the validated request with SHA-256 after normalizing timestamps to UTC and trimming the user reference. It acquires a nonblocking, transaction-scoped PostgreSQL advisory lock derived from the pair `(user_id, Idempotency-Key)`.

| Situation | Result |
| --- | --- |
| New key and successful allocation | Store the serialized JSON response and return 201. |
| Completed key and equivalent payload | Replay the stored status and body, including after changes to the room or confirmed deletion. |
| Existing key and different payload | Return `409 idempotency_key_reused` after acquiring the lock and comparing the fingerprint. |
| Same key currently locked or matching record still processing | Return `409 idempotency_request_in_progress`. The client can retry. |
| No available room | Return `409 no_available_room` and roll back the pending record. The same key can be retried later. |

The external `user_id` scopes deduplication. The service does not authenticate ownership of that value.

## Error handling

DRF validation errors, expected conflicts, and booking domain failures use the JSON envelope `{"error":{"code":"…","message":"…"}}`. Validation errors can include `error.fields`. The root Django URL configuration handles unmatched API routes and unexpected failures as JSON when `DEBUG=False`. An unexpected error keeps its original exception through transaction unwinding, then returns a generic 500 response.

## Logging

The middleware generates an HTTP request ID and returns it as `X-Request-ID`. JSON console logs record the HTTP method, route template, status, and error code when available. Booking events record creation, unavailability, replay, or rejection. Successful creation and replay share a persisted idempotency request UUID while each HTTP attempt has its own request ID. Unhandled database errors carry the exception type and request ID.

The formatter excludes raw URLs and query strings, request bodies, headers, idempotency keys, raw SQL, and exception text. `DJANGO_LOG_LEVEL` controls the console threshold. Docker Compose rotates web logs and restricts PostgreSQL's own error output.
