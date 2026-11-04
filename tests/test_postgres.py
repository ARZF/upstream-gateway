import os
from uuid import uuid4

from alembic import command
from alembic.config import Config
import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import RequestRecord
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
async def test_postgres_migration_startup_and_persistence(postgres_url):
    command.upgrade(Config("alembic.ini"), "head")
    app = create_app(
        Settings(
            database_url=postgres_url,
            provider_a_url="http://provider-a/v1/process",
            provider_b_url="http://provider-b/v1/process",
            provider_c_url="http://provider-c/v1/process",
        ),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"nested": [1, None]})
        ),
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
            request_id = response.json()["request_id"]
        with Session(app.state.database.engine) as session:
            record = session.get(RequestRecord, request_id)
            assert record.request_payload == {"json": [True, None]}
            assert record.provider_response == {"nested": [1, None]}
            assert record.created_at.utcoffset() is not None
        assert app.state.database.engine.pool.checkedout() == 0

    assert app.state.http_client.is_closed
