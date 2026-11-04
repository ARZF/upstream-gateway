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

One `requests` row records the final request outcome. JSON payload/response
columns remain SQLAlchemy `JSON`, and `created_at` remains a timezone-aware UTC
timestamp. PostgreSQL preserves its timezone semantics. Upstream calls and the
local database transaction cannot be atomic, so a provider side effect can
exist without a row if persistence fails.

## Deployment constraints

Compose starts PostgreSQL, waits for its health check, runs one Alembic migration
container, then starts the gateway. PostgreSQL data lives in a named volume and
port 5432 is not published.

The gateway must still run as one Uvicorn worker and one replica. Provider
statistics, cooldowns, semaphores, and admission limits are in memory. Moving
request history to PostgreSQL does not share this routing state.
