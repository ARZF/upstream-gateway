from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue


class ProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    payload: dict[str, JsonValue]


class ErrorDetails(BaseModel):
    code: str
    message: str


class ProcessResponse(BaseModel):
    request_id: UUID
    provider: str | None
    status: Literal["succeeded", "failed"]
    result: JsonValue = None
    latency_ms: float
    error: ErrorDetails | None = None
