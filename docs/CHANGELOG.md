# Changelog

## Unreleased

### Added

- Added `docs/CHANGELOG.md` to track repository changes.
- Added a high-level system and end-to-end data-flow diagram to `README.md`.
- Added configurable total request deadlines and request/response body limits.
- Added an index on request creation time and documented external retention.
- Added regression tests for mid-body transport failures and shared deadlines.
- Added a GitHub Actions test workflow backed by PostgreSQL 17.
- Added pending request records and durable per-provider attempt history.

### Changed

- Updated `AGENTS.md` to require reading the changelog before editing and adding an Unreleased entry for every task that changes repository files, including documentation-only changes.
- Migrated runtime persistence from SQLite to PostgreSQL with psycopg pooling, Alembic migrations, ordered Compose startup, and retained SQLite tests.
- Excluded upstream 4xx responses other than 429 from provider routing samples, scores, and cooldowns.
- Return 504 for total deadline expiry, 502 for oversized upstream responses, and 413 for oversized declared request bodies.
- Bound the Compose gateway port to localhost.
- Required provider URLs and restricted plain HTTP to loopback hosts.
- Changed provider identifier columns to unbounded text.
- Split and pinned direct runtime and development dependencies; shortcut: direct pins only, add a lock file or constraints when reproducibility of transitive packages matters.
- Updated `AGENTS.md` for PostgreSQL runtime persistence and editable pre-data initial migrations.
- Changed persistence to bracket provider calls with pending and completion transactions.
- Made request outcome columns nullable and return 503 when pending persistence is unavailable.

### Removed

- Removed the duplicate deployment guide and hard-coded provider URL defaults.
