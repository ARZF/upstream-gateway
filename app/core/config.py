import os

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    database_url: str = "postgresql+psycopg://localhost/upstream_gateway"
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)
    database_pool_timeout_seconds: float = Field(default=30.0, gt=0)
    provider_a_url: HttpUrl = HttpUrl("http://92.114.51.251:8001/v1/process")
    provider_b_url: HttpUrl = HttpUrl("http://92.114.51.251:8002/v1/process")
    provider_c_url: HttpUrl = HttpUrl("http://92.114.51.251:8003/v1/process")
    provider_a_concurrency: int = Field(default=10, ge=1)
    provider_b_concurrency: int = Field(default=10, ge=1)
    provider_c_concurrency: int = Field(default=10, ge=1)
    max_upstream_concurrency: int = Field(default=20, ge=1)
    http_max_connections: int = Field(default=30, ge=1)
    http_max_keepalive_connections: int = Field(default=20, ge=0)
    connect_timeout_seconds: float = Field(default=2.0, gt=0)
    read_timeout_seconds: float = Field(default=10.0, gt=0)
    write_timeout_seconds: float = Field(default=5.0, gt=0)
    pool_timeout_seconds: float = Field(default=1.0, gt=0)
    total_deadline_seconds: float = Field(default=15.0, gt=0)
    max_request_body_bytes: int = Field(default=1_048_576, ge=1)
    max_response_bytes: int = Field(default=1_048_576, ge=1)
    max_attempts: int = Field(default=2, ge=1, le=3)
    retry_ambiguous_failures: bool = False
    failure_cooldown_seconds: float = Field(default=5.0, gt=0)
    rate_limit_cooldown_seconds: float = Field(default=30.0, gt=0)
    stats_window: int = Field(default=50, ge=1)
    initial_latency_ms: float = Field(default=1000.0, gt=0)
    error_penalty_ms: float = Field(default=2000.0, ge=0)

    @model_validator(mode="after")
    def validate_pool(self) -> "Settings":
        if self.http_max_keepalive_connections > self.http_max_connections:
            raise ValueError(
                "Keep-alive connections cannot exceed total connections"
            )
        return self

    @classmethod
    def from_env(cls) -> "Settings":
        return cls.model_validate({
            name: os.environ[name.upper()]
            for name in cls.model_fields
            if name.upper() in os.environ
        })

    @property
    def providers(self) -> dict[str, tuple[str, int]]:
        return {
            "a": (str(self.provider_a_url), self.provider_a_concurrency),
            "b": (str(self.provider_b_url), self.provider_b_concurrency),
            "c": (str(self.provider_c_url), self.provider_c_concurrency),
        }
