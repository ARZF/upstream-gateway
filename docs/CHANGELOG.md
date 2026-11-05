# Changelog

## Unreleased

### Added

- Added `docs/CHANGELOG.md` to track repository changes.
- Added a high-level system and end-to-end data-flow diagram to `README.md`.
- Added configurable total request deadlines and request/response body limits.

### Changed

- Updated `AGENTS.md` to require reading the changelog before editing and adding an Unreleased entry for every task that changes repository files, including documentation-only changes.
- Migrated runtime persistence from SQLite to PostgreSQL with psycopg pooling, Alembic migrations, ordered Compose startup, and retained SQLite tests.
- Excluded upstream 4xx responses other than 429 from provider routing samples, scores, and cooldowns.
- Return 504 for total deadline expiry, 502 for oversized upstream responses, and 413 for oversized declared request bodies.
