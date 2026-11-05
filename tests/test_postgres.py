import os
from uuid import uuid4

from alembic import command
from alembic.config import Config
import httpx
import pytest
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.db.models import ProviderAttempt, RequestRecord
from app.main import create_app


@pytest.fixture
def postgres_url(monkeypatch):
    configured_url = os.environ.get("TEST_DATABASE_URL")
    if not configured_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    parsed = make_url(configured_url)
    if (
        parsed.get_backend_name() != "postgresql"
        or not (parsed.database or "").lower().endswith("_test")
    ):
        pytest.fail("TEST_DATABASE_URL must identify a database ending in _test")

    schema = f"gateway_{uuid4().hex}"
    admin_engine = create_engine(configured_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    test_url = parsed.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(
        hide_password=False
    )
    monkeypatch.setenv("DATABASE_URL", test_url)
    try:
        yield test_url
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_migration_startup_and_persistence(
    postgres_url, settings_factory
):
    identifier = "x" * 300
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.host == "provider-a":
            raise httpx.ConnectError("mock connection failure", request=request)
        return httpx.Response(200, json={
            "id": identifier,
            "request_id": identifier,
            "nested": [1, None],
        })

    command.upgrade(Config("alembic.ini"), "head")
    app = create_app(
        settings_factory(
            database_url=postgres_url,
        ),
        transport=httpx.MockTransport(handler),
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        ) as client:
            assert (await client.get("/ready")).status_code == 200
            response = await client.post(
                "/api/v1/process", json={"payload": {"json": [True, None]}}
            )
            assert response.status_code == 200
            assert calls == ["provider-a", "provider-b"]
            request_id = response.json()["request_id"]
        with Session(app.state.database.engine) as session:
            record = session.get(RequestRecord, request_id)
            assert record.request_payload == {"json": [True, None]}
            assert record.provider_response["nested"] == [1, None]
            assert record.provider_transaction_id == identifier
            assert record.provider_request_id == identifier
            assert record.created_at.utcoffset() is not None
            attempts = session.scalars(
                select(ProviderAttempt)
                .where(ProviderAttempt.request_id == request_id)
                .order_by(ProviderAttempt.attempt_number)
            ).all()
            assert len(attempts) == record.attempt_count == 2
            assert attempts[0].error_code == "connection_error"
            assert attempts[1].success
        with Session(app.state.database.engine) as session, session.begin():
            session.execute(
                delete(RequestRecord)
                .where(RequestRecord.request_id == request_id)
            )
        with Session(app.state.database.engine) as session:
            assert session.scalar(
                select(func.count()).select_from(ProviderAttempt)
            ) == 0
        assert app.state.database.engine.pool.checkedout() == 0

    assert app.state.http_client.is_closed
