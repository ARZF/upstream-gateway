from typing import Any

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base, RequestRecord


class Database:
    def __init__(
        self,
        url: str,
        *,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: float = 30.0,
    ) -> None:
        options: dict[str, Any] = {}
        parsed_url = make_url(url)
        backend = parsed_url.get_backend_name()
        if backend == "sqlite":
            options["connect_args"] = {
                "check_same_thread": False, "timeout": 5,
            }
            if parsed_url.database in {None, "", ":memory:"}:
                options["poolclass"] = StaticPool
        elif backend == "postgresql":
            options.update(
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=pool_timeout,
                pool_pre_ping=True,
            )
        self.engine = create_engine(url, **options)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", self._configure_sqlite)

    @staticmethod
    def _configure_sqlite(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    def initialize(self) -> None:
        if self.engine.dialect.name == "sqlite":
            # SQLite remains a self-contained test backend; PostgreSQL uses Alembic.
            Base.metadata.create_all(self.engine)
        self.check_connection()

    def check_connection(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(select(1).select_from(RequestRecord).limit(1))

    def save(self, record: RequestRecord) -> None:
        with Session(self.engine) as session, session.begin():
            session.add(record)

    def close(self) -> None:
        self.engine.dispose()
