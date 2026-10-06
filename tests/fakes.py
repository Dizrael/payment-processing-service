import asyncio
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from src.db.models import Currency
from src.services.errors import PublishRejectedError, WebhookDeliveryError
from src.services.gateway import GatewayResult
from src.services.webhook_sender import WebhookNotification


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[Mapping[str, Any], uuid.UUID]] = []
        self.reject_ids: set[uuid.UUID] = set()
        self.fail_with: Exception | None = None
        self._published_changed = asyncio.Condition()

    async def publish(self, payload: Mapping[str, Any], message_id: uuid.UUID) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        if message_id in self.reject_ids:
            raise PublishRejectedError(message_id)
        async with self._published_changed:
            self.published.append((payload, message_id))
            self._published_changed.notify_all()

    async def wait_for_published(self, count: int) -> None:
        async with self._published_changed:
            await self._published_changed.wait_for(lambda: len(self.published) >= count)


class FakeGateway:
    def __init__(self, result: GatewayResult | None = None) -> None:
        self.result = result or GatewayResult(succeeded=True)
        self.calls: list[tuple[uuid.UUID, Decimal, Currency]] = []

    async def charge(self, payment_id: uuid.UUID, amount: Decimal, currency: Currency) -> GatewayResult:
        self.calls.append((payment_id, amount, currency))
        return self.result


class FakeWebhookSender:
    def __init__(self, failures_before_success: int = 0) -> None:
        self.failures_left = failures_before_success
        self.attempts = 0
        self.delivered: list[tuple[str, WebhookNotification, str]] = []

    async def send(self, url: str, notification: WebhookNotification, message_id: str) -> None:
        self.attempts += 1
        if self.failures_left > 0:
            self.failures_left -= 1
            raise WebhookDeliveryError(url, 500)
        self.delivered.append((url, notification, message_id))
