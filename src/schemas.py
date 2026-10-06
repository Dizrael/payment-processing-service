import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from src.db.models import Currency, PaymentStatus

AMOUNT_QUANTUM = Decimal("0.01")
MAX_METADATA_BYTES = 4096
AMOUNT_EXAMPLE = "1500.00"
DESCRIPTION_EXAMPLE = "Order #1024"
METADATA_EXAMPLE = {"order_id": "A-1024"}
WEBHOOK_URL_EXAMPLE = "https://merchant.example/webhooks/payments"


class PaymentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2, examples=[AMOUNT_EXAMPLE])]
    currency: Currency
    description: Annotated[str, Field(min_length=1, max_length=500, examples=[DESCRIPTION_EXAMPLE])]
    meta: dict[str, Any] = Field(default_factory=dict, alias="metadata", examples=[METADATA_EXAMPLE])
    webhook_url: Annotated[HttpUrl, Field(examples=[WEBHOOK_URL_EXAMPLE])]

    @field_validator("amount")
    @classmethod
    def normalize_amount(cls, value: Decimal) -> Decimal:
        return value.quantize(AMOUNT_QUANTUM)

    @field_validator("meta")
    @classmethod
    def limit_metadata_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        size = len(json.dumps(value, separators=(",", ":")).encode())
        if size > MAX_METADATA_BYTES:
            msg = f"metadata must not exceed {MAX_METADATA_BYTES} bytes when serialized"
            raise ValueError(msg)
        return value


class PaymentAcceptedResponse(BaseModel):
    id: uuid.UUID
    status: PaymentStatus
    created_at: datetime


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    amount: Annotated[Decimal, Field(examples=[AMOUNT_EXAMPLE])]
    currency: Currency
    description: Annotated[str, Field(examples=[DESCRIPTION_EXAMPLE])]
    meta: Annotated[dict[str, Any], Field(serialization_alias="metadata", examples=[METADATA_EXAMPLE])]
    status: PaymentStatus
    failure_reason: str | None
    idempotency_key: uuid.UUID
    webhook_url: Annotated[str, Field(examples=[WEBHOOK_URL_EXAMPLE])]
    created_at: datetime
    processed_at: datetime | None
