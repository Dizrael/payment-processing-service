import asyncio
import contextlib
import logging
import secrets
import signal
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.db.models import Currency
from src.logger import LogLevel, configure_logging
from src.schemas import PaymentCreateRequest

PAYMENTS_PATH = "/api/v1/payments"
SHUTDOWN_SIGNALS = (signal.SIGINT, signal.SIGTERM)

logger = logging.getLogger(__name__)


class OrderGeneratorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORDER_GENERATOR_", env_file=".env", extra="ignore")

    api_url: str = "http://localhost:8000"
    api_key: SecretStr = Field(validation_alias="API_KEY", min_length=1)
    webhook_url: str = "http://localhost:8080/webhooks"
    rate_per_second: float = Field(default=2, gt=0)
    total: int = Field(default=0, ge=0)
    duplicate_rate: float = Field(default=0.1, ge=0, le=1)
    request_timeout_seconds: float = Field(default=10, gt=0)
    log_level: LogLevel = Field(default="INFO", validation_alias="LOG_LEVEL")


@dataclass(frozen=True, slots=True)
class Order:
    idempotency_key: uuid.UUID
    body: dict[str, Any]


class OrderGenerator:
    def __init__(self, client: httpx.AsyncClient, webhook_url: str, duplicate_rate: float) -> None:
        self._client = client
        self._webhook_url = webhook_url
        self._duplicate_rate = duplicate_rate
        self._rng = secrets.SystemRandom()

    def new_order(self) -> Order:
        order_id = f"GEN-{self._rng.randrange(1_000_000):06d}"
        request = PaymentCreateRequest.model_validate(
            {
                "amount": Decimal(self._rng.randint(100, 10_000_000)).scaleb(-2),
                "currency": self._rng.choice(list(Currency)),
                "description": f"Order {order_id}",
                "metadata": {"order_id": order_id},
                "webhook_url": self._webhook_url,
            }
        )
        return Order(idempotency_key=uuid.uuid4(), body=request.model_dump(mode="json", by_alias=True))

    def next_order(self, previous: Order | None) -> Order:
        if previous is not None and self._rng.random() < self._duplicate_rate:
            return previous
        return self.new_order()

    async def submit(self, order: Order, *, duplicate: bool) -> None:
        log_fields: dict[str, Any] = {"idempotency_key": str(order.idempotency_key), "duplicate": duplicate}
        try:
            response = await self._client.post(
                PAYMENTS_PATH,
                json=order.body,
                headers={"Idempotency-Key": str(order.idempotency_key)},
            )
        except httpx.HTTPError as error:
            logger.warning("Order submission failed", extra={**log_fields, "error": repr(error)})
            return
        if not response.is_success:
            logger.warning("Order rejected", extra={**log_fields, "status_code": response.status_code})
            return
        logger.info(
            "Order submitted",
            extra={**log_fields, "status_code": response.status_code, "payment_id": response.json()["id"]},
        )

    async def run(self, stop_event: asyncio.Event, total: int, interval_seconds: float) -> int:
        submitted = 0
        previous: Order | None = None
        while not stop_event.is_set() and (total == 0 or submitted < total):
            order = self.next_order(previous)
            await self.submit(order, duplicate=order is previous)
            previous = order
            submitted += 1
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), interval_seconds)
        return submitted


async def run_generator(settings: OrderGeneratorSettings) -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in SHUTDOWN_SIGNALS:
        loop.add_signal_handler(signal_number, stop_event.set)
    try:
        async with httpx.AsyncClient(
            base_url=settings.api_url,
            headers={"X-API-Key": settings.api_key.get_secret_value()},
            timeout=settings.request_timeout_seconds,
        ) as client:
            generator = OrderGenerator(client, settings.webhook_url, settings.duplicate_rate)
            logger.info(
                "Order generator started",
                extra={"rate_per_second": settings.rate_per_second, "total": settings.total},
            )
            submitted = await generator.run(stop_event, settings.total, 1 / settings.rate_per_second)
    finally:
        for signal_number in SHUTDOWN_SIGNALS:
            loop.remove_signal_handler(signal_number)
    logger.info("Order generator stopped", extra={"submitted": submitted})


if __name__ == "__main__":
    generator_settings = OrderGeneratorSettings()
    configure_logging(generator_settings.log_level)
    asyncio.run(run_generator(generator_settings))
