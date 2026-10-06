import uuid
from collections.abc import Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import Payment, PaymentStatus
from src.services.errors import PaymentNotFoundError, WebhookDeliveryError
from src.services.gateway import DECLINED_REASON, GatewayResult
from src.services.processing_service import ProcessingService, RetryPolicy
from src.services.webhook_sender import WebhookNotification
from tests.factories import generate_payment
from tests.fakes import FakeGateway, FakeWebhookSender
from tests.sentinels import DATETIME_NOW


class RecordingSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


class CountingSessionFactory:
    def __init__(self, inner: async_sessionmaker[AsyncSession]) -> None:
        self.inner = inner
        self.calls = 0

    def __call__(self) -> AsyncSession:
        self.calls += 1
        return self.inner()


def build_service(
    session_factory: Callable[[], AsyncSession],
    gateway: FakeGateway,
    webhook_sender: FakeWebhookSender,
    sleep: RecordingSleep,
) -> ProcessingService:
    return ProcessingService(
        session_factory=session_factory,
        gateway=gateway,
        webhook_sender=webhook_sender,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=2, sleep=sleep),
    )


async def get_payment(session_factory: async_sessionmaker[AsyncSession], payment_id: uuid.UUID) -> Payment:
    async with session_factory() as session:
        return await session.get_one(Payment, payment_id)


def notification_for(payment: Payment) -> WebhookNotification:
    return WebhookNotification(
        payment_id=payment.id,
        status=payment.status,
        amount=payment.amount,
        currency=payment.currency,
        failure_reason=payment.failure_reason,
        processed_at=payment.processed_at,
    )


async def test_successful_charge_marks_payment_succeeded_and_sends_webhook(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payment = await get_payment(session_factory, (await generate_payment(session_factory)).id)
    gateway, webhooks, sleep = FakeGateway(), FakeWebhookSender(), RecordingSleep()

    await build_service(session_factory, gateway, webhooks, sleep).process(payment.id, "message-1")

    stored = await get_payment(session_factory, payment.id)
    assert {
        "payment": stored.column_values(),
        "gateway_calls": gateway.calls,
        "webhooks": webhooks.delivered,
        "delays": sleep.delays,
    } == {
        "payment": {**payment.column_values(), "status": PaymentStatus.SUCCEEDED, "processed_at": DATETIME_NOW},
        "gateway_calls": [(payment.id, payment.amount, payment.currency)],
        "webhooks": [(payment.webhook_url, notification_for(stored), "message-1")],
        "delays": [],
    }


async def test_declined_charge_marks_payment_failed_with_reason(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payment = await get_payment(session_factory, (await generate_payment(session_factory)).id)
    gateway = FakeGateway(GatewayResult(succeeded=False, failure_reason=DECLINED_REASON))
    webhooks = FakeWebhookSender()

    await build_service(session_factory, gateway, webhooks, RecordingSleep()).process(payment.id, "message-1")

    stored = await get_payment(session_factory, payment.id)
    assert {
        "payment": stored.column_values(),
        "gateway_calls": gateway.calls,
        "webhooks": webhooks.delivered,
    } == {
        "payment": {
            **payment.column_values(),
            "status": PaymentStatus.FAILED,
            "failure_reason": DECLINED_REASON,
            "processed_at": DATETIME_NOW,
        },
        "gateway_calls": [(payment.id, payment.amount, payment.currency)],
        "webhooks": [(payment.webhook_url, notification_for(stored), "message-1")],
    }


async def test_already_processed_payment_skips_gateway_but_sends_webhook(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payment = await get_payment(
        session_factory, (await generate_payment(session_factory, status=PaymentStatus.SUCCEEDED)).id
    )
    gateway, webhooks = FakeGateway(), FakeWebhookSender()

    await build_service(session_factory, gateway, webhooks, RecordingSleep()).process(payment.id, "message-1")

    assert {
        "payment": (await get_payment(session_factory, payment.id)).column_values(),
        "gateway_calls": gateway.calls,
        "webhooks": webhooks.delivered,
    } == {
        "payment": payment.column_values(),
        "gateway_calls": [],
        "webhooks": [(payment.webhook_url, notification_for(payment), "message-1")],
    }


async def test_webhook_failures_are_retried_without_recharging(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payment = await generate_payment(session_factory)
    gateway, webhooks, sleep = FakeGateway(), FakeWebhookSender(failures_before_success=2), RecordingSleep()

    await build_service(session_factory, gateway, webhooks, sleep).process(payment.id, "message-1")

    stored = await get_payment(session_factory, payment.id)
    assert {
        "gateway_calls": gateway.calls,
        "webhook_attempts": webhooks.attempts,
        "webhooks": webhooks.delivered,
        "delays": sleep.delays,
    } == {
        "gateway_calls": [(payment.id, payment.amount, payment.currency)],
        "webhook_attempts": 3,
        "webhooks": [(payment.webhook_url, notification_for(stored), "message-1")],
        "delays": [2, 4],
    }


async def test_exhausted_retries_raise_and_keep_committed_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payment = await get_payment(session_factory, (await generate_payment(session_factory)).id)
    gateway, webhooks, sleep = FakeGateway(), FakeWebhookSender(failures_before_success=10), RecordingSleep()

    with pytest.raises(WebhookDeliveryError):
        await build_service(session_factory, gateway, webhooks, sleep).process(payment.id, "message-1")

    assert {
        "payment": (await get_payment(session_factory, payment.id)).column_values(),
        "gateway_calls": gateway.calls,
        "webhook_attempts": webhooks.attempts,
        "webhooks": webhooks.delivered,
        "delays": sleep.delays,
    } == {
        "payment": {**payment.column_values(), "status": PaymentStatus.SUCCEEDED, "processed_at": DATETIME_NOW},
        "gateway_calls": [(payment.id, payment.amount, payment.currency)],
        "webhook_attempts": 3,
        "webhooks": [],
        "delays": [2, 4],
    }


async def test_missing_payment_is_not_retried(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    counting = CountingSessionFactory(session_factory)
    gateway, webhooks, sleep = FakeGateway(), FakeWebhookSender(), RecordingSleep()

    with pytest.raises(PaymentNotFoundError):
        await build_service(counting, gateway, webhooks, sleep).process(uuid.uuid4(), "message-1")

    assert {
        "session_calls": counting.calls,
        "gateway_calls": gateway.calls,
        "webhook_attempts": webhooks.attempts,
        "delays": sleep.delays,
    } == {
        "session_calls": 1,
        "gateway_calls": [],
        "webhook_attempts": 0,
        "delays": [],
    }
