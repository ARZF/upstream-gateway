# PostgreSQL Deployment

## Configuration

Create `.env` from `.env.example` without overwriting an existing file. Set a
strong `POSTGRES_PASSWORD`, keep `POSTGRES_DB` and `POSTGRES_USER` aligned with
`DATABASE_URL`, and use the `postgresql+psycopg://` scheme. In Compose the host
is `postgres`; for an independently installed local server it is commonly
`localhost`. Percent-encode URL-reserved characters only in `DATABASE_URL`.

The application reads process environment variables through
`Settings.from_env()`. It does not load `.env` itself. Compose loads `.env` with
`env_file`.

## Compose deployment

```sh
docker compose up -d --build --wait
docker compose ps
docker compose logs migrate
docker compose logs -f gateway
```

PostgreSQL must become healthy, then the `migrate` service must complete an
Alembic upgrade before the gateway starts. The gateway readiness check verifies
database access without writes or provider calls.

Stop containers without deleting PostgreSQL data:

```sh
docker compose down
```

Do not use `docker compose down -v` unless permanent deletion of request history
is intended. Back up `postgres_data` using normal PostgreSQL backup procedures;
a Docker volume is not a backup.

## Non-Compose deployment

Export all variables in `.env`, then run the migration and one application
worker:

```sh
python -m alembic upgrade head
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Run migrations as one explicit deployment job, not concurrently in every app
worker. A missing table or unavailable database causes startup to fail.

## Rollback and inspection

Review a migration's downgrade before use. `python -m alembic downgrade -1`
may destroy data and is not an automatic deployment recovery strategy. Prefer a
forward fix after taking a backup.

Inspect the schema and recent rows with `psql`:

```sh
python -m alembic current
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\\d+ requests"'
```

See `README.md` for test commands.
