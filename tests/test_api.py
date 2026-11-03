import asyncio
import json
import socket
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from time import monotonic
from uuid import UUID

import httpx
import pytest
import uvicorn
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.database import Database
from app.db.models import RequestRecord
from app.main import create_app


@pytest.fixture
def open_api(tmp_path):
    @asynccontextmanager
    async def open_client(handler, **overrides):
        settings = Settings(
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            provider_a_url="http://provider-a/v1/process",
            provider_b_url="http://provider-b/v1/process",
            provider_c_url="http://provider-c/v1/process",
            **overrides,
        )
        app = create_app(settings, transport=httpx.MockTransport(handler))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://gateway"
            ) as client:
                yield app, client
        assert app.state.http_client.is_closed

    return open_client


def get_record(app, request_id):
    with Session(app.state.database.engine) as session:
        record = session.get(RequestRecord, request_id)
        assert record is not None
        session.expunge(record)
        return record


@pytest.mark.asyncio
async def test_health_endpoints_do_not_call_providers_or_store_records(open_api):
    def handler(_):
        pytest.fail("Health probes must not call providers")

    async with open_api(handler) as (app, client):
        for path, status in [("/health", "ok"), ("/ready", "ready")]:
            response = await client.get(path)
            assert response.status_code == 200
            assert response.json() == {"status": status}
            UUID(response.headers["X-Request-ID"])
        with Session(app.state.database.engine) as session:
            assert session.scalar(select(func.count()).select_from(RequestRecord)) == 0
        schema = (await client.get("/openapi.json")).json()
        assert "/health" in schema["paths"]
        assert "503" in schema["paths"]["/ready"]["get"]["responses"]


@pytest.mark.asyncio
async def test_readiness_tracks_startup_and_shutdown(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'lifecycle.db'}"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    ) as client:
        response = await client.get("/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
        assert (await client.get("/health")).status_code == 200
        async with app.router.lifespan_context(app):
            assert (await client.get("/ready")).status_code == 200
        response = await client.get("/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
        assert (await client.get("/health")).status_code == 200
        assert app.state.http_client.is_closed


@pytest.mark.asyncio
async def test_readiness_database_failure_and_recovery(open_api, monkeypatch, caplog):
    def connect():
        raise OperationalError(
            "secret SQL", {"password": "private-value"},
            Exception("private database path"),
        )

    async with open_api(lambda _: pytest.fail("Unexpected provider call")) as (app, client):
        with monkeypatch.context() as patch:
            patch.setattr(app.state.database.engine, "connect", connect)
            response = await client.get("/ready")
            assert response.status_code == 503
            assert response.json() == {"status": "not_ready"}
            assert (await client.get("/health")).status_code == 200
        assert (await client.get("/ready")).status_code == 200
        assert "private" not in caplog.text
        assert "secret SQL" not in caplog.text


@pytest.mark.asyncio
async def test_readiness_fails_when_requests_table_is_unavailable(open_api):
    async with open_api(lambda _: pytest.fail("Unexpected provider call")) as (app, client):
        with app.state.database.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE requests")
        response = await client.get("/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
        assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
async def test_readiness_fails_when_http_client_is_closed(open_api):
    async with open_api(lambda _: pytest.fail("Unexpected provider call")) as (app, client):
        await app.state.http_client.aclose()
        assert (await client.get("/ready")).status_code == 503
        assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
async def test_failed_database_initialization_leaves_app_not_ready(tmp_path, monkeypatch):
    def initialize(_):
        raise OperationalError("initialize", {}, Exception("private database path"))

    monkeypatch.setattr(Database, "initialize", initialize)
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'failed.db'}"))
    with pytest.raises(OperationalError):
        async with app.router.lifespan_context(app):
            pytest.fail("Startup must not succeed")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    ) as client:
        response = await client.get("/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
        assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
async def test_success_and_persistence(open_api, caplog):
    calls = []
    payload = {"nested": [{"anything": None}, True, 42], "secret": "not-in-logs"}
    provider_response = {
        "id": "txn_123", "status": "approved", "provider": "a",
        "echo": payload, "latency_ms": 17.5, "request_id": "upstream-id",
        "extra": [1, 2],
    }

    def handler(request):
        calls.append(request)
        assert json.loads(request.content) == {"payload": payload}
        return httpx.Response(200, json=provider_response)

    async with open_api(handler) as (app, client):
        with caplog.at_level("INFO", logger="upstream_gateway"):
            response = await client.post("/api/v1/process", json={"payload": payload})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "succeeded"
        assert data["provider"] == "a"
        assert data["result"] == provider_response
        UUID(data["request_id"])
        assert response.headers["X-Request-ID"] == data["request_id"]
        assert calls[0].headers["X-Request-ID"] == data["request_id"]
        record = get_record(app, data["request_id"])
        assert record.request_payload == payload
        assert record.provider_response == provider_response
        assert record.provider_transaction_id == "txn_123"
        assert record.provider_request_id == "upstream-id"
        assert record.http_status_code == record.upstream_http_status_code == 200
        assert record.processing_status == "succeeded"
        assert record.total_latency_ms > 0
        assert record.created_at is not None
        assert record.error_details is None
        assert record.attempt_count == 1
        assert app.state.smart_router.providers["a"].successful_requests == 1
        assert len(calls) == 1
        assert "not-in-logs" not in caplog.text


@pytest.mark.asyncio
async def test_sqlite_data_survives_app_recreation(open_api):
    async with open_api(lambda _: httpx.Response(200, json={})) as (_, client):
        response = await client.post(
            "/api/v1/process", json={"payload": {"marker": "persistent"}}
        )
        assert response.status_code == 200
        request_id = response.json()["request_id"]

    def handler(_):
        pytest.fail("The readiness probe must not call providers")

    async with open_api(handler) as (app, client):
        response = await client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}
        record = get_record(app, request_id)
        assert record.request_payload == {"marker": "persistent"}
        assert record.processing_status == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_payload", [{}, [1, None], "ok", None])
async def test_preserves_variable_json_response(open_api, provider_payload):
    def handler(_):
        return httpx.Response(200, content=json.dumps(provider_payload))

    async with open_api(handler) as (app, client):
        response = await client.post("/api/v1/process", json={"payload": {}})
        assert response.status_code == 200
        assert response.json()["result"] == provider_payload
        record = get_record(app, response.json()["request_id"])
        assert record.provider_transaction_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,expected", [("timeout", 504), ("server", 502)])
async def test_ambiguous_failures_not_retried_by_default(open_api, kind, expected):
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if kind == "timeout":
            raise httpx.ReadTimeout("mock timeout", request=request)
        return httpx.Response(500, json={"error": "provider failed"})

    async with open_api(handler) as (app, client):
        response = await client.post("/api/v1/process", json={"payload": {}})
        assert response.status_code == expected
        assert calls == ["provider-a"]
        provider = app.state.smart_router.providers["a"]
        assert provider.failed_requests == 1
        assert provider.active_requests == 0
        assert not provider.available
        record = get_record(app, response.json()["request_id"])
        assert record.processing_status == "failed"
        assert record.http_status_code == expected
        assert record.error_details["code"] == response.json()["error"]["code"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["connection", "timeout", "server", "rate_limit"])
async def test_fallback(open_api, kind):
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.host == "provider-a":
            if kind == "connection":
                raise httpx.ConnectError("mock error", request=request)
            if kind == "timeout":
                raise httpx.ReadTimeout("mock timeout", request=request)
            if kind == "rate_limit":
                return httpx.Response(429, headers={"Retry-After": "120"})
            return httpx.Response(503, json={"error": "mock"})
        return httpx.Response(200, json={"id": "fallback"})

    async with open_api(handler, retry_ambiguous_failures=True) as (app, client):
        response = await client.post("/api/v1/process", json={"payload": {}})
        assert response.status_code == 200
        assert response.json()["provider"] == "b"
        assert calls == ["provider-a", "provider-b"]
        record = get_record(app, response.json()["request_id"])
        assert record.selected_provider == "b"
        assert record.attempt_count == 2
        assert record.error_details is None
        assert app.state.smart_router.providers["a"].failed_requests == 1
        assert app.state.smart_router.providers["b"].successful_requests == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", ["120", "date", "bad"])
async def test_429_cooldown_and_no_immediate_reuse(open_api, retry_after):
    if retry_after == "date":
        retry_after = format_datetime(datetime.now(UTC) + timedelta(seconds=120))
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.host == "provider-a":
            return httpx.Response(429, headers={"Retry-After": retry_after})
        return httpx.Response(200, json={})

    async with open_api(handler) as (app, client):
        before = monotonic()
        for _ in range(2):
            response = await client.post("/api/v1/process", json={"payload": {}})
            assert response.status_code == 200
        provider = app.state.smart_router.providers["a"]
        assert provider.rate_limited
        assert provider.rate_limited_until >= before + (29 if retry_after == "bad" else 118)
        assert calls == ["provider-a", "provider-b", "provider-b"]


@pytest.mark.asyncio
async def test_retry_attempts_are_bounded(open_api):
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(500, json={"error": "mock failure"})

    async with open_api(handler, retry_ambiguous_failures=True) as (app, client):
        response = await client.post("/api/v1/process", json={"payload": {}})
        assert response.status_code == 502
        assert calls == ["provider-a", "provider-b"]
        assert get_record(app, response.json()["request_id"]).attempt_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 404, 422])
async def test_nonretryable_4xx(open_api, status):
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(status, json={"detail": "rejected"})

    async with open_api(handler, retry_ambiguous_failures=True) as (app, client):
        response = await client.post("/api/v1/process", json={"payload": {}})
        assert response.status_code == status
        assert calls == ["provider-a"]
        assert response.json()["result"] == {"detail": "rejected"}
        assert app.state.smart_router.providers["a"].available


@pytest.mark.asyncio
async def test_no_available_providers_persisted(open_api):
    def handler(_):
        pytest.fail("Unavailable providers must not be called")

    async with open_api(handler) as (app, client):
        for provider in app.state.smart_router.providers.values():
            provider.unavailable_until = monotonic() + 100
        response = await client.post("/api/v1/process", json={"payload": {}})
        assert response.status_code == 503
        record = get_record(app, response.json()["request_id"])
        assert record.selected_provider is None
        assert record.attempt_count == 0
        assert record.http_status_code == 503


@pytest.mark.asyncio
async def test_no_provider_capacity_after_first_failure(open_api):
    def handler(request):
        raise httpx.ConnectError("mock error", request=request)

    async with open_api(handler) as (app, client):
        for name in ("b", "c"):
            app.state.smart_router.providers[name].unavailable_until = monotonic() + 100
        response = await client.post("/api/v1/process", json={"payload": {}})
        assert response.status_code == 503
        assert get_record(app, response.json()["request_id"]).attempt_count == 1


@pytest.mark.asyncio
async def test_all_rate_limited(open_api):
    async with open_api(lambda _: httpx.Response(429)) as (app, client):
        response = await client.post("/api/v1/process", json={"payload": {}})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "rate_limited"
        assert get_record(app, response.json()["request_id"]).upstream_http_status_code == 429


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"not json", b'{"a": NaN}', b'{"a": 1e400}'])
async def test_invalid_upstream_json(open_api, content):
    async with open_api(lambda _: httpx.Response(200, content=content)) as (app, client):
        response = await client.post("/api/v1/process", json={"payload": {}})
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "invalid_response"
        assert not app.state.smart_router.providers["a"].available


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"payload": []}, {"payload": {}, "extra": 1}])
async def test_invalid_request_has_id_and_no_upstream_call(open_api, body):
    def handler(_):
        pytest.fail("Validation errors must not call providers")

    async with open_api(handler) as (_, client):
        response = await client.post("/api/v1/process", json=body)
        assert response.status_code == 422
        UUID(response.headers["X-Request-ID"])


@pytest.mark.asyncio
async def test_database_failure_is_explicit(open_api, monkeypatch):
    def save(_):
        raise OperationalError("insert", {}, Exception("mock disk failure"))

    async with open_api(lambda _: httpx.Response(200, json={})) as (app, client):
        monkeypatch.setattr(app.state.database, "save", save)
        response = await client.post("/api/v1/process", json={"payload": {}})
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "persistence_error"
        assert "may have processed" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_overload_rejected_without_waiting(open_api):
    entered = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request.url.host)
        entered.set()
        await finish.wait()
        return httpx.Response(200, json={})

    async with open_api(handler, max_upstream_concurrency=1) as (app, client):
        first = asyncio.create_task(client.post("/api/v1/process", json={"payload": {}}))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            second = await asyncio.wait_for(
                client.post("/api/v1/process", json={"payload": {}}), timeout=2
            )
            assert second.status_code == 503
            assert calls == ["provider-a"]
        finally:
            finish.set()
            assert (await first).status_code == 200
        assert not app.state.smart_router.semaphore.locked()


@pytest.mark.asyncio
async def test_uvicorn_startup_and_endpoint_over_real_socket(tmp_path):
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'smoke.db'}"),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"id": "smoke"})),
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_config=None))
        task = asyncio.create_task(server.serve(sockets=[sock]))

        async def wait_for_start():
            while not server.started:
                if task.done():
                    await task
                    raise AssertionError("Uvicorn exited before startup")
                await asyncio.sleep(0.01)

        try:
            await asyncio.wait_for(wait_for_start(), 5)
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", trust_env=False
            ) as client:
                schema = await client.get("/openapi.json")
                assert "/api/v1/process" in schema.json()["paths"]
                assert (await client.get("/health")).status_code == 200
                assert (await client.get("/ready")).status_code == 200
                response = await client.post("/api/v1/process", json={"payload": {}})
                assert response.status_code == 200
                assert response.json()["result"] == {"id": "smoke"}
                assert get_record(app, response.json()["request_id"]).processing_status == "succeeded"
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 5)
    assert app.state.http_client.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("number", ["NaN", "Infinity", "1e400"])
async def test_nonfinite_request_values_rejected(open_api, number):
    def handler(_):
        pytest.fail("Invalid numbers must not reach providers")

    async with open_api(handler) as (_, client):
        response = await client.post(
            "/api/v1/process", content='{"payload":{"number":' + number + '}}',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422
