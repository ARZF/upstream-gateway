# Changelog

## Unreleased

### Added

- Added `docs/CHANGELOG.md` to track repository changes.
- Added a high-level system and end-to-end data-flow diagram to `README.md`.

### Changed

- Updated `AGENTS.md` to require reading the changelog before editing and adding an Unreleased entry for every task that changes repository files, including documentation-only changes.
- Migrated runtime persistence from SQLite to PostgreSQL with psycopg pooling, Alembic migrations, ordered Compose startup, and retained SQLite tests.
