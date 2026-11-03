from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base, RequestRecord


class Database:
    def __init__(self, url: str) -> None:
        options: dict[str, Any] = {}
        if url.startswith("sqlite:"):
            options["connect_args"] = {
                "check_same_thread": False, "timeout": 5,
            }
            if url in {"sqlite://", "sqlite:///:memory:"}:
                options["poolclass"] = StaticPool
        self.engine = create_engine(url, **options)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", self._configure_sqlite)

    @staticmethod
    def _configure_sqlite(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    def initialize(self) -> None:
        # shortcut: create_all cannot alter tables, use migrations for changes.
        Base.metadata.create_all(self.engine)

    def save(self, record: RequestRecord) -> None:
        with Session(self.engine) as session, session.begin():
            session.add(record)

    def close(self) -> None:
        self.engine.dispose()
