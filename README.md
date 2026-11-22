# Hotel Booking System

## Project Overview

A REST API for managing hotel rooms and booking an available room for a requested time period. The service matches the requested bed count and view, assigns a room, and returns the reservation details. PostgreSQL enforces the no-overlap rule even when concurrent requests compete for the same room.

## Features

- **Room management** — Create, retrieve, update, and delete rooms. List rooms with pagination and exact filters for bed count and view. Deleting a room with reservations requires explicit confirmation.
- **Deterministic room allocation** — Assign the lowest-ID available room that matches the requested features. Bookings use timezone-aware timestamps and allow back-to-back stays.
- **Concurrency-safe reservations** — Transactions and row locks coordinate room allocation, and a PostgreSQL exclusion constraint prevents overlapping bookings for the same room.
- **Idempotent booking requests** — Successful requests can be retried with the same key and payload to recover the original status and response. Reusing a key with a different payload returns a conflict.
- **Request tracing** — Structured JSON logs correlate API outcomes, booking events, and database failures with the `X-Request-ID` returned to the client.

## Tech Stack

| Technology | Role |
| --- | --- |
| Python 3.12 | Application runtime |
| Django 5.2 LTS | Application framework, ORM, and migrations |
| Django REST Framework | REST endpoints, serialization, and validation |
| PostgreSQL 16 | Persistent storage, transactional locking, and exclusion constraints |
| Docker Compose | Local application and database environment |

## Architecture

See the [architecture documentation](docs/architecture.md) for the database design, transaction boundaries, and concurrency controls.

The project is a modular monolith. `rooms` owns room management and its deletion policy. `bookings` owns reservation validation, room allocation, and Idempotency. `config` contains shared routing, exception handling, settings, and request logging.

Serializers validate HTTP input, and the booking service performs room selection and writes. After locking a candidate room, the service checks availability again before inserting the booking. The database enforces the same no-overlap invariant through a GiST exclusion constraint. The reservation and its successful response snapshot commit in one transaction. A PostgreSQL advisory lock coordinates requests that use the same `(user_id, Idempotency-Key)` pair.

`user_id` is a reference supplied by the caller. The public API does not provide user accounts or verify ownership of this identifier.

## Requirements

- Git and a shell. The examples below use PowerShell.
- For local Python execution, Python 3.12 and a PostgreSQL database. Docker Compose v2 can run the database by itself.
- For a fully containerized setup, Docker with Compose v2.
- The migration role needs permission to install the PostgreSQL `btree_gist` extension if it is absent. The test role also needs permission to create a test database.

## Installation

Run the following from the repository root:

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Set `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD` in `.env`. To run PostgreSQL with Compose while Django runs on the host, set `POSTGRES_HOST=127.0.0.1` and `POSTGRES_PORT=5433`, then run:

```powershell
docker compose up -d --wait db
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

If you already run PostgreSQL, use its connection settings in `.env` and run the two Django commands. The service is available at `http://localhost:8000`. Check that the application is responding at [`/health/`](http://localhost:8000/health/).

## Environment Variables

Django reads configuration from `.env` and the process environment. The repository includes [`.env.example`](.env.example) as a starting point.

| Variable | Purpose | Local setting |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | Required secret used by Django | A generated secret |
| `DJANGO_DEBUG` | Enables Django development diagnostics | `True` |
| `DJANGO_SECURE_HTTPS` | Enables HTTPS redirects, secure cookies, and HSTS | `False` for local HTTP |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames allowed by Django | `localhost,127.0.0.1` |
| `DJANGO_LOG_LEVEL` | Minimum console log level | `INFO` |
| `POSTGRES_DB` | Database name | `hotel_booking` |
| `POSTGRES_USER` | Database user | `hotel_booking` |
| `POSTGRES_PASSWORD` | Database password | A local password shared by Django and PostgreSQL |
| `POSTGRES_HOST` | Database host | `127.0.0.1` on the host, `db` inside Compose |
| `POSTGRES_PORT` | Database port | `5433` from the host with Compose, `5432` inside Compose |

Keep the actual `.env` out of version control.

## Docker Setup

Copy `.env.example` to `.env` if needed and set the Django secret and database password. Then run:

```powershell
docker compose up -d --build --wait
docker compose exec web python manage.py migrate
docker compose logs -f web
```

Compose publishes the web service on port `8000` and PostgreSQL on `127.0.0.1:5433`. The web container connects to `db:5432`. This setup uses Django's development server.

## API Documentation

See the [API reference](docs/api.md) for headers, payloads, response examples, and validation rules.

Open the [Browsable API root](http://localhost:8000/api/v1/), [Rooms API](http://localhost:8000/api/v1/rooms/), or [Booking API](http://localhost:8000/api/v1/bookings/) after starting the service.

| Method | Endpoint | Behavior |
| --- | --- | --- |
| `GET` | `/api/v1/rooms/` | List rooms. Supports `beds`, `view`, `page`, and `page_size`. |
| `POST` | `/api/v1/rooms/` | Create a room with a `label`, `beds`, and `view`. |
| `GET` | `/api/v1/rooms/{id}/` | Retrieve a room. |
| `PUT`, `PATCH` | `/api/v1/rooms/{id}/` | Replace or partially update a room. |
| `DELETE` | `/api/v1/rooms/{id}/` | Delete a room. With existing bookings, `?confirm=true` explicitly authorizes deletion of both the room and those bookings. |
| `POST` | `/api/v1/bookings/` | Create a reservation and return the assigned room. Requires an `Idempotency-Key` header. |
| `GET` | `/health/` | Check that the application responds. |

To try a booking, first create a room with two beds and a `sea` view. Then send the following JSON to `POST /api/v1/bookings/` with `Content-Type: application/json` and `Idempotency-Key: booking-demo-001`:

```json
{
  "user_id": "guest-42",
  "beds": 2,
  "view": "sea",
  "check_in": "2027-07-10T14:00:00Z",
  "check_out": "2027-07-12T11:00:00Z"
}
```

A successful request returns `201 Created` with the booking and room details. Invalid input returns `400`, and unavailable rooms or conflicting Idempotency requests return `409`. Domain errors include a machine-readable `error.code`.

## Running Tests

Run the full suite against PostgreSQL, either locally or inside the web container:

```powershell
.\.venv\Scripts\python.exe manage.py test
# Or, with the Compose services running:
docker compose exec web python manage.py test
```

Django creates a separate test database, applies migrations, and removes it after the run. The suite covers API behavior, database constraints, transaction rollback, concurrent bookings, Idempotency, and log redaction. PostgreSQL is required to exercise row locking, advisory locks, and the exclusion constraint.

## Project Structure

```text
.
|-- bookings/              Booking API, allocation service, Idempotency, migrations, tests
|-- rooms/                 Room API, filters, pagination, migrations, tests
|-- config/                Django settings, routing, error handling, request logging
|-- .env.example           Local configuration template
|-- docker-compose.yml     Web and PostgreSQL services
|-- Dockerfile             Python application image
|-- manage.py              Django management commands
|-- requirements.txt      Runtime dependencies
`-- requirements-dev.txt  Development dependencies
```
