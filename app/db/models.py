from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RequestRecord(Base):
    __tablename__ = "requests"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    selected_provider: Mapped[str | None] = mapped_column(String(16))
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    provider_response: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    provider_transaction_id: Mapped[str | None] = mapped_column(Text)
    provider_request_id: Mapped[str | None] = mapped_column(Text)
    http_status_code: Mapped[int | None] = mapped_column(Integer)
    upstream_http_status_code: Mapped[int | None] = mapped_column(Integer)
    processing_status: Mapped[str] = mapped_column(String(32))
    total_latency_ms: Mapped[float | None] = mapped_column(Float)
    error_details: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    attempt_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class ProviderAttempt(Base):
    __tablename__ = "provider_attempts"
    __table_args__ = (
        UniqueConstraint(
            "request_id", "attempt_number",
            name="uq_provider_attempts_request_attempt",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("requests.request_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    upstream_status_code: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
