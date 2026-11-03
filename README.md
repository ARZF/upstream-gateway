# Upstream Gateway

A Python 3.14+ FastAPI backend that forwards `POST /api/v1/process` to one of
three existing HTTP providers. It records metadata for successful and failed
validated requests in SQLite. There are no provider implementations or live
health probes in this project.

## Install and run

Windows PowerShell:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Linux/macOS with Python 3.14+:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

SQLite tables are created at startup in `gateway.db`. The shared HTTP client
and database engine are closed during shutdown. Invalid configuration or
database initialization errors prevent startup. Interactive API docs are at
<http://127.0.0.1:8000/docs>.

## Configuration

All settings in `app/core/config.py` are configurable with uppercase environment
variables. `.env.example` lists every setting and its default. The app reads the
process environment, not `.env` files; no dotenv dependency is needed.

To apply the supplied file in PowerShell (only load files you trust):

```powershell
Copy-Item .env.example .env
# Edit .env before loading it.
Get-Content .env | ForEach-Object {
    if ($_ -match '^([A-Z_]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}
.\.venv\Scripts\python.exe -m uvicorn app.main:app --workers 1
```

For POSIX shells:

```sh
cp .env.example .env
# Edit .env before loading it. Sourcing a file executes shell code; trust the file.
set -a
. ./.env
set +a
.venv/bin/python -m uvicorn app.main:app --workers 1
```

Provider URLs are full process endpoint URLs and default to:

- A: `http://92.114.51.251:8001/v1/process`
- B: `http://92.114.51.251:8002/v1/process`
- C: `http://92.114.51.251:8003/v1/process`

The defaults allow 10 concurrent calls per provider, 20 concurrent upstream
calls overall, and at most two distinct provider attempts per incoming request.
`MAX_ATTEMPTS=1` disables fallback, and the maximum permitted value is 3.
Connection/read/write/pool timeouts default to 2/10/5/1 seconds respectively.
HTTPX uses a pool of 30 connections with 20 keep-alive connections. Redirects,
environment proxies, and implicit transport retries are disabled.

## API usage

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
- 504: upstream read/write timeout.
- 502: connection/transport failure, upstream 5xx, invalid successful JSON,
  or unexpected redirect. Redirects are not followed.
- Other upstream 4xx: forwarded status with the JSON error body in `result`,
  without fallback. Validation failures at the gateway use FastAPI's normal
  422 `detail` response (without echoing invalid input) and do not create
  database records.
- 500: metadata storage failed. The response explicitly warns that the provider
  may already have processed the request. Do not automatically resubmit it.

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
- `app/db/`: SQLAlchemy 2.x engine, short-lived transactional sessions, and
  portable JSON/string/date columns. Synchronous SQLite operations run in
  Starlette's worker thread pool instead of blocking the async event loop.
- `app/core/`: validated environment configuration and standard-library JSON
  logging; payloads and raw exception bodies are not logged.

### Routing and concurrency

Providers in a failure cooldown or rate-limit cooldown, providers at capacity,
and providers already attempted by this request are excluded. Candidates are
scored as:

```text
average_latency_ms + error_penalty_ms * (recent_failures / recent_attempts)
```

The last 50 completed attempts per provider form a bounded statistics window.
Cancelled attempts count as failures but do not quarantine the provider. All
observed HTTP errors count as failures for scoring, including client 4xx. New
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
timezone marker. WAL mode and a 5-second busy timeout reduce local writer
contention. No SQLAlchemy session is shared between requests.

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

Tests use HTTPX `MockTransport` exclusively for external APIs and temporary
SQLite databases. They cover success, selection, varying responses, timeouts,
429 seconds/HTTP-date cooldowns, 5xx, fallback bounds, non-retryable 4xx, no
providers, persistence errors, overload, cancellation, and invalid inputs.
The Uvicorn smoke test starts a real loopback HTTP listener and invokes the
endpoint with mocked upstreams, checking startup, persistence, and shutdown.
Tests never contact the real provider servers.

## Initial-version limits

- Run **one worker**. Runtime health/statistics/semaphores are process-local;
  multiple workers or replicas would multiply the configured limits. Shared
  admission/routing state is needed before scaling that way.
- This is a local backend, not an authenticated public API. Add authentication,
  TLS, incoming body/traffic limits, retention, and access controls before
  internet exposure. The supplied provider URLs use unencrypted HTTP; do not
  send sensitive production data without a secure provider connection.
- SQLite and `create_all()` are local-development choices. A PostgreSQL move
  requires a PostgreSQL driver, a connection URL, and schema migrations; the
  model does not depend on SQLite-specific column types.
- An HTTP read timeout bounds individual read inactivity, not total duration of
  a continuously streaming response. No total deadline or response-size cap
  is implemented; add those before accepting untrusted/large upstream bodies.
- Upstream side effects and local persistence cannot be atomic. Cancellation,
  a crash, or a failed commit after a provider call can leave an external effect
  without a database record. Durable intake/idempotency is needed for stronger
  guarantees. This version does not claim exactly-once processing.
- No distributed infrastructure, background health polling, tracing, metrics
  endpoints, or additional public business endpoints are included.
