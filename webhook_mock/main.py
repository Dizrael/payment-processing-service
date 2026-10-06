import logging
import random
import secrets
from collections import deque
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

from src.logger import LogLevel, configure_logging

MAX_STORED_WEBHOOKS = 1000

logger = logging.getLogger(__name__)


class WebhookMockSettings(BaseSettings):
    webhook_mock_failure_rate: float = Field(default=0.3, ge=0, le=1)
    log_level: LogLevel = "INFO"


class ReceivedWebhook(BaseModel):
    message_id: str | None
    payload: Any
    received_at: datetime


def create_app(failure_rate: float, rng: random.Random | None = None) -> FastAPI:
    random_source = rng or secrets.SystemRandom()
    received: deque[ReceivedWebhook] = deque(maxlen=MAX_STORED_WEBHOOKS)
    app = FastAPI(title="Webhook mock")

    @app.post("/webhooks")
    async def receive_webhook(request: Request) -> JSONResponse:
        message_id = request.headers.get("X-Message-Id")
        if random_source.random() < failure_rate:
            logger.warning("Simulated webhook failure", extra={"message_id": message_id})
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Simulated failure"},
            )
        received.append(
            ReceivedWebhook(
                message_id=message_id,
                payload=await request.json(),
                received_at=datetime.now(UTC),
            )
        )
        logger.info("Webhook received", extra={"message_id": message_id, "payload": received[-1].payload})
        return JSONResponse(content={"status": "received"})

    @app.get("/webhooks")
    async def list_webhooks() -> list[ReceivedWebhook]:
        return list(received)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


mock_settings = WebhookMockSettings()
configure_logging(mock_settings.log_level)
app = create_app(mock_settings.webhook_mock_failure_rate)
