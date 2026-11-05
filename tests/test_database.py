from datetime import UTC, datetime
from unittest.mock import Mock

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, delete, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Database
from app.db.models import ProviderAttempt, RequestRecord
from app.services.provider_service import AttemptRecord


def make_record(request_id: str, **overrides) -> RequestRecord:
    values = {
        "request_id": request_id,
        "request_payload": {"nested": [1, None]},
        "processing_status": "pending",
        "created_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    }
    values.update(overrides)
    return RequestRecord(**values)


def completed_values(**overrides):
    values = {
        "selected_provider": "a",
        "provider_response": ["ok", {"value": True}],
        "provider_transaction_id": "transaction-id",
        "provider_request_id": "provider-request-id",
        "http_status_code": 200,
        "upstream_http_status_code": 200,
        "processing_status": "succeeded",
        "total_latency_ms": 12.5,
        "error_details": None,
        "attempt_count": 1,
    }
    values.update(overrides)
    return values


def make_attempt(attempt_number=1, **overrides):
    values = {
        "attempt_number": attempt_number,
        "provider": "a",
        "upstream_status_code": 200,
        "latency_ms": 10.0,
        "success": True,
        "error_code": None,
    }
    values.update(overrides)
    return AttemptRecord(**values)


def test_sqlite_initialize_persistence_and_cleanup(tmp_path, monkeypatch):
    database = Database(f"sqlite:///{tmp_path / 'database.db'}")
    database.initialize()
    database.create_pending(make_record("request-1"))
    database.complete(
        "request-1", completed_values(), [make_attempt()]
    )

    with Session(database.engine) as session:
        record = session.get(RequestRecord, "request-1")
        assert record is not None
        assert record.request_payload == {"nested": [1, None]}
        assert record.provider_response == ["ok", {"value": True}]
        assert session.scalar(
            select(func.count()).select_from(ProviderAttempt)
        ) == 1

    dispose = Mock(wraps=database.engine.dispose)
    monkeypatch.setattr(database.engine, "dispose", dispose)
    database.close()
    dispose.assert_called_once_with()


def test_sqlite_driver_url_keeps_in_memory_database_between_sessions():
    database = Database("sqlite+pysqlite:///:memory:")
    try:
        assert isinstance(database.engine.pool, StaticPool)
        database.initialize()
        database.create_pending(make_record("in-memory"))
        with Session(database.engine) as session:
            assert session.get(RequestRecord, "in-memory") is not None
    finally:
        database.close()


def test_alembic_upgrade_creates_current_schema(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'alembic.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    command.check(Config("alembic.ini"))

    engine = create_engine(url)
    try:
        assert inspect(engine).has_table("requests")
        assert inspect(engine).has_table("provider_attempts")
        assert set(RequestRecord.__table__.columns.keys()) == {
            column["name"] for column in inspect(engine).get_columns("requests")
        }
        assert set(ProviderAttempt.__table__.columns.keys()) == {
            column["name"]
            for column in inspect(engine).get_columns("provider_attempts")
        }
        assert "ix_requests_created_at" in {
            index["name"] for index in inspect(engine).get_indexes("requests")
        }
    finally:
        engine.dispose()


def test_attempt_numbers_are_unique_and_delete_cascades(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'constraints.db'}")
    database.initialize()
    try:
        database.create_pending(make_record("request-1"))
        with pytest.raises(IntegrityError):
            database.complete(
                "request-1", completed_values(attempt_count=2),
                [make_attempt(), make_attempt(provider="b")],
            )

        database.complete(
            "request-1", completed_values(), [make_attempt()]
        )
        with Session(database.engine) as session, session.begin():
            session.execute(
                delete(RequestRecord)
                .where(RequestRecord.request_id == "request-1")
            )
        with Session(database.engine) as session:
            assert session.scalar(
                select(func.count()).select_from(ProviderAttempt)
            ) == 0
    finally:
        database.close()
