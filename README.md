# Upstream Gateway

A Python 3.14+ FastAPI backend that forwards `POST /api/v1/process` to one of
three existing HTTP providers. It records metadata for successful and failed
validated requests in PostgreSQL. There are no provider implementations or live
provider health probes in this project.

## Install and run

Windows PowerShell:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Linux/macOS with Python 3.14+:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

These host commands require `DATABASE_URL` to point to a running PostgreSQL
server; change the Compose hostname `postgres` in `.env.example` to `localhost`
when appropriate. Alembic creates and upgrades the schema explicitly. The app
does not alter PostgreSQL schemas at startup. The shared HTTP client and database
engine are closed during shutdown. Invalid configuration, unavailable databases,
or missing migrations prevent startup. Interactive API docs are at
<http://127.0.0.1:8000/docs>.

## Configuration

All settings in `app/core/config.py` are configurable with uppercase environment
variables. `.env.example` documents every setting, with example provider URLs
and defaults for the other settings. Keep it as a tracked template; put actual
runtime configuration in the untracked `.env`. The app reads the process
environment through `Settings.from_env()`, not `.env` files; no dotenv
dependency is needed. Docker Compose loads `.env` into the container environment.

PostgreSQL requires `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and a
matching `DATABASE_URL` using the `postgresql+psycopg://` scheme. SQLAlchemy's
pool defaults to 5 persistent connections, 10 overflow connections, and a
30-second checkout timeout. Configure these with `DATABASE_POOL_SIZE`,
`DATABASE_MAX_OVERFLOW`, and `DATABASE_POOL_TIMEOUT_SECONDS`.

To apply the supplied file in PowerShell (only load files you trust):

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# Edit .env with real provider URLs before loading it.
Get-Content .env | ForEach-Object {
    if ($_ -match '^([A-Z_]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}
.\.venv\Scripts\python.exe -m uvicorn app.main:app --workers 1
```

For POSIX shells:

```sh
if [ ! -e .env ]; then cp .env.example .env; fi
# Edit .env with real provider URLs. Sourcing executes shell code; trust the file.
set -a
. ./.env
set +a
.venv/bin/python -m uvicorn app.main:app --workers 1
```

All three provider URLs are required full process endpoint URLs. HTTPS is always
allowed. Plain HTTP is accepted only for `localhost`, `127.0.0.1`, or `::1`.

Default limits allow 10 concurrent calls per provider, 20 concurrent upstream
calls overall, and at most two distinct provider attempts per incoming request.
`MAX_ATTEMPTS=1` disables fallback, and the maximum permitted value is 3.
Connection/read/write/pool timeouts default to 2/10/5/1 seconds respectively.
All provider attempts share a 15-second total deadline. Incoming request bodies
and upstream response bodies default to a 1 MiB limit; the incoming limit checks
`Content-Length`, while the response limit is enforced during streaming.
HTTPX uses a pool of 30 connections with 20 keep-alive connections. Redirects,
environment proxies, and implicit transport retries are disabled.

## Docker

### Prerequisites and environment

Install Docker Engine with the Docker Compose v2 plugin, or Docker Desktop
using Linux containers. Start the Docker engine before running these commands.
No host Python installation is required.

Create `.env` from `.env.example` **only if `.env` does not already exist**,
using the copy command for your shell in the configuration section above. Edit
`.env` with a strong PostgreSQL password, a matching psycopg connection URL,
actual provider endpoint URLs, and desired concurrency/timeouts.
The template's `example.com` URLs are placeholders, not working providers.
Existing environment files are not renamed or overwritten. `.env` is ignored
by Git and Docker; it is never copied into the image. `.env.example` stays tracked.

Compose uses `env_file: .env` to supply runtime variables. `DATABASE_URL` must
use `postgres` as its hostname because that is the Compose database service
name. Missing optional settings retain application defaults; missing provider
URLs or invalid settings prevent startup. If credentials contain URL-reserved
characters, percent-encode them in `DATABASE_URL` while keeping their literal
values in `POSTGRES_PASSWORD`.

Provider URLs must be reachable **from the container**. `127.0.0.1`, `::1`, and
`localhost` refer to the gateway container, not the host. Providers elsewhere,
including `host.docker.internal`, must expose HTTPS.

### Build, start, and inspect

From the repository root (commands work in both PowerShell and POSIX shells):

```sh
docker compose up -d --build --wait
docker compose ps
docker compose logs -f gateway
```

The API is available at <http://localhost:8000>, with docs at
<http://localhost:8000/docs>. Compose publishes port 8000 on localhost only.
An authenticated TLS reverse proxy is required before any wider exposure.

The gateway image uses official Python 3.14 slim, caches the dependency
installation layer separately from application code, and runs as UID/GID 10001.
PostgreSQL uses the official PostgreSQL 17 image without publishing port 5432.
The one-shot `migrate` service waits for PostgreSQL health, runs `alembic upgrade
head`, and must finish successfully before the gateway starts.
Uvicorn runs as the main process with **one worker**, no reload, and lifespan
enabled. Do not add workers or scale replicas: routing state and concurrency
limits are process-local. A 30-second stop grace period allows normal request
draining and the existing HTTPX/database shutdown handlers to run.

The health check uses Python's standard library to GET `/ready` locally,
bypassing environment proxies. Connection errors, timeouts, and HTTP errors
fail the check. No additional dependency is required. It checks startup and
database accessibility without calling providers or writing database rows.
Docker marks an unhealthy container but does not restart it solely for being
unhealthy.

To safely check the process route without invoking real providers:

```sh
curl -i -X POST http://localhost:8000/api/v1/process -H "Content-Type: application/json" -d "{}"
```

Use `curl.exe` in PowerShell. Expect **422** for the missing `payload` and an
`X-Request-ID` header. Use the API examples above only when real provider calls
and their possible side effects are intended.

### Stop and rebuild

```sh
docker compose down
```

This removes the container and network but retains the database volume. To
rebuild after code/dependency changes and recreate the service:

```sh
docker compose build --pull gateway migrate
docker compose up -d --wait --force-recreate
```

### PostgreSQL persistence

Compose creates a named volume, `postgres_data` (normally prefixed with the
Compose project name), mounted at PostgreSQL's data directory. Data survives
container recreation and ordinary `docker compose down`/`up`. Router statistics
and cooldowns are intentionally reset on restart.

**Do not run `docker compose down -v` or delete/prune this volume unless you
intend to delete the database.** Keep the same Compose project name when
restarting; a different name creates a different volume. The volume contains
request/provider payloads and must be protected and backed up as sensitive data.
A volume provides persistence, not backups.

Inspect recent records without publishing PostgreSQL's port:

```sh
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT request_id, processing_status, created_at FROM requests ORDER BY created_at DESC LIMIT 20"'
```

### Data retention

Choose a retention period based on operational and legal needs; 30 days is an
example, not a default. Run deletion from a scheduled external job such as cron
or `pg_cron` rather than from every gateway process:

```sh
docker compose exec postgres psql -U gateway -d upstream_gateway -c "DELETE FROM requests WHERE created_at < now() - interval '30 days';"
```

Adjust the database user, database name, and period for the deployment. Request
and provider payloads are sensitive. PostgreSQL autovacuum, or a planned manual
`VACUUM`, reclaims space after deletion.

### Schema migrations

Run migrations before every application deployment:

```sh
python -m alembic current
python -m alembic upgrade head
```

Use the repository virtual environment on host systems as shown above. Compose
runs the upgrade through its controlled one-shot `migrate` service. The gateway
never calls `create_all()` for PostgreSQL, so multiple application processes do
not race to modify schemas. Create future revisions with `python -m alembic
revision --autogenerate -m "description"` and review generated SQL before use.

Existing SQLite files are left untouched and are not imported into PostgreSQL.

## API usage

### Health endpoints

- `GET /health`: liveness, returns **200** with `{"status":"ok"}` when the
  application can serve requests. It does not access the database or providers.
- `GET /ready`: readiness, returns **200** with `{"status":"ready"}` only
  after startup completes, while the shared HTTP client is open, and a
  read-only query against PostgreSQL's `requests` table succeeds. It returns
  **503** with `{"status":"not_ready"}` before initialization, during
  shutdown, or when a required local dependency is unavailable. Database
  checks run in the existing worker thread pool, not on the async event loop.

Both endpoints appear in `/docs` and `/openapi.json`, include `X-Request-ID`,
and expose no configuration or internal error details. Neither calls upstream
providers or records probe requests in PostgreSQL. Readiness verifies database
access and table availability, not write capacity, free disk space, or upstream
provider availability. Startup failures still prevent Uvicorn from serving.

```sh
curl -i http://localhost:8000/health
curl -i http://localhost:8000/ready
```

Use `curl.exe` in PowerShell.

### Process requests

The outer object must contain `payload`, which is an object. Its nested values
may be any valid JSON: objects, arrays, strings, finite numbers, booleans, or
null. Unknown outer fields and non-object payloads receive 422.

POSIX curl:

```sh
curl -i http://127.0.0.1:8000/api/v1/process \
  -H 'Content-Type: application/json' \
  -d '{"payload":{"order_id":"example-order","items":[{"sku":"example","quantity":1}]}}'
```

PowerShell curl (use `curl.exe`, not the legacy PowerShell alias):

```powershell
'{"payload":{"order_id":"example-order","items":[]}}' | curl.exe -i http://127.0.0.1:8000/api/v1/process -H 'Content-Type: application/json' --data-binary '@-'
```

Live curl requests use the real providers and may have real effects. Use the
test suite for safe mocked verification.

Response shape (illustrative placeholders, not a promised provider result):

```json
{
  "request_id": "00000000-0000-4000-8000-000000000000",
  "provider": "a",
  "status": "succeeded",
  "result": {"id": "example-provider-transaction", "status": "approved"},
  "latency_ms": 123.0,
  "error": null
}
```

`result` preserves the provider's JSON without imposing transaction fields or
status values. `succeeded` means a valid JSON HTTP 2xx response was received,
not that the provider approved a business operation. The gateway returns 200
for successful processing and stores the actual upstream status separately.
Every request gets a new server-generated UUID in `X-Request-ID`, even when
validation fails; caller-supplied IDs are not trusted. The same UUID is forwarded
to providers as `X-Request-ID`, but this is **not** an idempotency guarantee.

Failure responses use the same envelope with `status: "failed"` and an
`error` object containing `code` and a safe message:

- 503: no eligible provider, exhausted capacity, or upstream rate limiting.
- 504: upstream read/write timeout or total provider-attempt deadline exceeded.
- 502: connection/transport failure, upstream 5xx, invalid successful JSON,
  oversized upstream response, or unexpected redirect. Redirects are not
  followed.
- 413: declared incoming request body exceeds the configured limit; it is
  rejected before validation, provider calls, or persistence.
- Other upstream 4xx: forwarded status with the JSON error body in `result`,
  without fallback. Validation failures at the gateway use FastAPI's normal
  422 `detail` response (without echoing invalid input) and do not create
  database records.
- 500: metadata storage failed. The response explicitly warns that the provider
  may already have processed the request. Do not automatically resubmit it.

## System flow

![Upstream Gateway system components and end-to-end data flow](docs/diagrams/upstream-gateway-architecture.svg)

The editable diagram source is
[`docs/diagrams/upstream-gateway-architecture.html`](docs/diagrams/upstream-gateway-architecture.html).

## Architecture and decisions

- `app/main.py`: application factory, request IDs/logging middleware, lifespan,
  one pooled `httpx.AsyncClient`, and dependency wiring.
- `app/api/routes.py`: validates the request, invokes the service, persists the
  outcome, and serializes the public response.
- `app/services/provider_service.py`: bounded attempt loop, status translation,
  per-attempt logging, runtime updates, and guaranteed capacity release.
- `app/services/smart_router.py`: process-local runtime state, admission, and
  rule-based scoring. No network calls happen here.
- `app/clients/provider_client.py`: HTTP calls, JSON validation, error categories,
  and `Retry-After` parsing.
- `app/db/`: synchronous SQLAlchemy 2.x engine, bounded PostgreSQL connection
  pool, short-lived transactional sessions, and portable JSON/string/date
  columns. Database operations run in Starlette's worker thread pool instead of
  blocking the async event loop. Alembic owns PostgreSQL schema changes.
- `app/core/`: validated environment configuration and standard-library JSON
  logging; payloads and raw exception bodies are not logged.

### Routing and concurrency

Providers in a failure cooldown or rate-limit cooldown, providers at capacity,
and providers already attempted by this request are excluded. Candidates are
scored as:

```text
average_latency_ms + error_penalty_ms * (recent_failures / recent_attempts)
```

The last 50 scored attempts per provider form a bounded statistics window.
Cancelled attempts count as failures but do not quarantine the provider. Upstream
4xx responses other than 429 do not affect samples, scores, or cooldowns; 429
and other provider errors still count as failures for scoring. New
providers start with a 1000 ms latency estimate and no failures. Ties follow
configuration order (A, B, C). There is no background probing or periodic
exploration: untried providers get traffic when their score or availability
makes them the best eligible candidate. Cooldowns expire lazily on selection.

An `asyncio.Semaphore` protects each provider and another protects total
upstream concurrency. Admission checks and acquisition happen without yielding
on a single event loop. No caller joins a provider-capacity queue; overload is
rejected immediately. Capacity is released in `finally`, including cancellation.
These are concurrency limits, **not** requests-per-second limits.

### Conservative fallback

Connection establishment failures and HTTP 429 can fall back to a different
provider. Other transport failures, read/write timeouts, and HTTP 5xx are
retryable in principle but may occur after a POST caused an external effect.
Their fallback is **disabled by default**. Enable `RETRY_AMBIGUOUS_FAILURES=true`
only if the operation is safe to repeat or providers provide a verified
cross-provider idempotency mechanism. A request ID alone does not provide one.

Each provider is attempted at most once per incoming request, bounded by
`MAX_ATTEMPTS`. There are no same-provider retry loops, sleeps holding capacity,
or extra health checks. Connection/transport/timeout/5xx failures temporarily
quarantine the provider for 5 seconds, even when fallback is disabled.

429 uses either seconds or an HTTP-date from `Retry-After`, with a default
30-second cooldown when absent/invalid. Its monotonic deadline is not shortened
by later in-flight 429 responses. No incoming request waits for that deadline;
it falls back or receives 503. Non-retryable 4xx do not trigger a cooldown.

### Persistence and logging

One database row stores the final attempted provider, request payload, final
provider JSON, optional transaction/provider request IDs, gateway and upstream
HTTP statuses, processing status, latency, safe error details, attempt count,
and UTC creation timestamp. Earlier attempts are represented in correlated
logs and runtime statistics, not separate database rows. Persisted latency is
measured at the persistence boundary; response latency includes the database
commit as well. SQLite timestamps are UTC, though SQLite does not preserve the
timezone marker. PostgreSQL preserves timezone-aware timestamps. No SQLAlchemy
session or connection is shared between requests, and checked-in connections are
validated before reuse. SQLite remains supported for isolated tests, including
its WAL and busy-timeout configuration.

Logs are JSON events for incoming requests, provider selection, upstream start
and end, response status, latency, fallback, and errors, correlated by UUID.
Provider/client payloads are stored in the database as requested but never
logged in full. Protect the database as sensitive data.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

```sh
.venv/bin/python -m pytest -q
```

GitHub Actions runs the same suite against PostgreSQL 17 on every push and pull
request, including the PostgreSQL-marked integration test.

Tests use HTTPX `MockTransport` exclusively for external APIs and temporary
SQLite databases. They cover success, selection, varying responses, timeouts,
429 seconds/HTTP-date cooldowns, 5xx, fallback bounds, non-retryable 4xx, no
providers, persistence errors, overload, cancellation, and invalid inputs.
Health tests cover liveness, startup/shutdown readiness, closed HTTP clients,
database connection failure/recovery, missing tables, and safe error responses.
The Uvicorn smoke test starts a real loopback HTTP listener and invokes the
endpoint with mocked upstreams, checking startup, persistence, and shutdown.
Tests never contact the real provider servers.

An optional PostgreSQL integration test exercises Alembic, application startup,
JSON persistence, timezone-aware timestamps, and cleanup in a temporary schema.
It only runs when `TEST_DATABASE_URL` names a PostgreSQL database ending in
`_test`:

```sh
TEST_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/upstream_gateway_test python -m pytest -q -m postgres
```

## Initial-version limits

- Run **one worker**. Runtime health/statistics/semaphores are process-local;
  multiple workers or replicas would multiply the configured limits. Shared
  admission/routing state is needed before scaling that way.
- This is a local backend, not an authenticated public API. Add authentication,
  TLS, chunked-upload enforcement, traffic limits, retention, and access controls
  before internet exposure. Do not send sensitive production data without secure
  provider connections.
- PostgreSQL makes request history shareable, but router statistics, cooldowns,
  semaphores, and concurrency limits remain process-local. Keep one Uvicorn
  worker and one gateway replica until routing state and admission are shared.
- A configurable total deadline bounds all provider attempts, and upstream
  response bodies are stream-counted against a configurable cap. Incoming body
  limits rely on `Content-Length`; chunked uploads must be capped by a reverse
  proxy or a future stream-counting implementation before public exposure.
- Upstream side effects and local persistence cannot be atomic. Cancellation,
  a crash, or a failed commit after a provider call can leave an external effect
  without a database record. Durable intake/idempotency is needed for stronger
  guarantees. This version does not claim exactly-once processing.
- No distributed infrastructure, background health polling, tracing, metrics
  endpoints, or additional public business endpoints are included.
