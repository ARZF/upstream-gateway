# Architecture

## Runtime

FastAPI owns one application lifespan containing a shared asynchronous HTTPX
client, a synchronous SQLAlchemy `Database`, and the process-local SmartRouter.
API handlers keep database work off the event loop with `run_in_threadpool`.
Sessions are short-lived and transactional; no session is shared between
requests.

PostgreSQL is the default persistence backend through psycopg 3. SQLAlchemy uses
a queue pool with configurable size, overflow, and checkout timeout, plus
connection liveness checks before reuse. Startup verifies that the migrated
`requests` table is readable, and shutdown disposes the engine. Connection or
schema failures prevent the application from becoming ready without exposing
raw database errors through health responses.

## Schema ownership

`app/db/models.py` is the SQLAlchemy metadata source. Alembic revisions in
`alembic/versions/` are the only normal PostgreSQL schema-change mechanism.
Deployments run `alembic upgrade head` before starting the gateway. The
application does not create or alter PostgreSQL tables automatically.

SQLite support remains in `Database` for temporary unit-test databases. Only
SQLite connections receive `check_same_thread`, busy-timeout, WAL, and in-memory
`StaticPool` handling.

## Persistence boundary

After validation, the first short transaction inserts a `requests` row with
`processing_status="pending"` before any provider call. If this insert fails,
the provider is not called. After routing finishes, a second transaction updates
the outcome and inserts every `provider_attempts` row, so the final request and
its attempt history commit together. Deleting a request cascades to its attempts.

Cancellation, a crash, or a failed completion leaves the pending row as a
durable trace; automatic reconciliation is not implemented. JSON
payload/response columns remain SQLAlchemy `JSON`, and timestamps remain
timezone-aware UTC values on PostgreSQL. The two transactions per request keep
database work explicit but add latency that still needs load testing.

## Deployment constraints

Compose starts PostgreSQL, waits for its health check, runs one Alembic migration
container, then starts the gateway. PostgreSQL data lives in a named volume and
port 5432 is not published.

The gateway must still run as one Uvicorn worker and one replica. Provider
statistics, cooldowns, semaphores, and admission limits are in memory. Moving
request history to PostgreSQL does not share this routing state.
