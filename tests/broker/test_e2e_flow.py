import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aio_pika
import httpx
import pytest
import respx
from aio_pika.abc import AbstractRobustConnection
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.broker.config import NEW_PAYMENTS_QUEUE_NAME, PAYMENTS_EXCHANGE_NAME, create_broker, declare_topology
from src.broker.consumer import PaymentProcessor, register_payment_consumer
from src.broker.publisher import RabbitPaymentPublisher
from src.config import Settings
from src.db.models import Payment, PaymentOutbox
from src.services.outbox_relay import OutboxRelay
from src.services.processing_service import ProcessingService, RetryPolicy
from src.services.webhook_sender import HttpxWebhookSender
from tests.broker.amqp import receive_dead_letter
from tests.fakes import FakeGateway

pytestmark = [pytest.mark.e2e, pytest.mark.xdist_group("rabbitmq")]

WEBHOOK_URL = "https://merchant.test/webhooks"
WEBHOOK_WAIT_SECONDS = 15
BODY: dict[str, object] = {
    "amount": "10.00",
    "currency": "EUR",
    "description": "E2E payment",
    "webhook_url": WEBHOOK_URL,
}


@asynccontextmanager
async def running_consumer(settings: Settings, processor: PaymentProcessor) -> AsyncIterator[None]:
    broker = create_broker(settings)
    register_payment_consumer(broker, settings, processor)
    await broker.connect()
    await declare_topology(broker, settings)
    await broker.start()
    try:
        yield
    finally:
        await broker.stop()


async def relay_once(settings: Settings, session_factory: async_sessionmaker[AsyncSession]) -> None:
    broker = create_broker(settings)
    await broker.connect()
    try:
        await declare_topology(broker, settings)
        await OutboxRelay(session_factory, RabbitPaymentPublisher(broker), settings.outbox_batch_size).relay_batch()
    finally:
        await broker.stop()


@asynccontextmanager
async def processing_service(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[ProcessingService]:
    async with httpx.AsyncClient(trust_env=False) as http_client:
        yield ProcessingService(
            session_factory=session_factory,
            gateway=FakeGateway(),
            webhook_sender=HttpxWebhookSender(http_client, trusted_hosts=frozenset({"merchant.test"})),
            retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0),
        )


async def create_payment_via_api(client: httpx.AsyncClient) -> uuid.UUID:
    response = await client.post("/api/v1/payments", json=BODY, headers={"Idempotency-Key": str(uuid.uuid4())})
    return uuid.UUID(response.json()["id"])


async def get_payment(session_factory: async_sessionmaker[AsyncSession], payment_id: uuid.UUID) -> Payment:
    async with session_factory() as session:
        return await session.get_one(Payment, payment_id)


async def get_outbox_entry(session_factory: async_sessionmaker[AsyncSession]) -> PaymentOutbox:
    async with session_factory() as session:
        return (await session.scalars(select(PaymentOutbox))).one()


@pytest.mark.usefixtures("amqp_connection")
async def test_payment_flows_from_api_to_webhook(
    client: httpx.AsyncClient,
    e2e_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    delivered: asyncio.Future[httpx.Request] = asyncio.get_running_loop().create_future()

    def accept(request: httpx.Request) -> httpx.Response:
        delivered.set_result(request)
        return httpx.Response(200)

    payment_id = await create_payment_via_api(client)
    with respx.mock(assert_all_called=True) as router:
        router.post(WEBHOOK_URL).mock(side_effect=accept)
        async with (
            processing_service(session_factory) as service,
            running_consumer(e2e_settings, service),
        ):
            await relay_once(e2e_settings, session_factory)
            webhook = await asyncio.wait_for(delivered, WEBHOOK_WAIT_SECONDS)

    payment = await get_payment(session_factory, payment_id)
    entry = await get_outbox_entry(session_factory)
    assert {
        "status": payment.status,
        "processed": payment.processed_at is not None,
        "outbox_published": entry.published_at is not None,
        "webhook_message_id": webhook.headers["X-Message-Id"],
        "webhook_payload": json.loads(webhook.content),
    } == {
        "status": "succeeded",
        "processed": True,
        "outbox_published": True,
        "webhook_message_id": str(entry.id),
        "webhook_payload": {
            "payment_id": str(payment_id),
            "status": "succeeded",
            "amount": "10.00",
            "currency": "EUR",
            "failure_reason": None,
            "processed_at": payment.processed_at.isoformat() if payment.processed_at else None,
        },
    }


async def test_failing_webhook_moves_message_to_dead_letter_queue(
    client: httpx.AsyncClient,
    e2e_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    amqp_connection: AbstractRobustConnection,
) -> None:
    payment_id = await create_payment_via_api(client)
    with respx.mock(assert_all_called=True) as router:
        webhook = router.post(WEBHOOK_URL).mock(return_value=httpx.Response(500))
        async with (
            processing_service(session_factory) as service,
            running_consumer(e2e_settings, service),
        ):
            await relay_once(e2e_settings, session_factory)
            dead_letter = await receive_dead_letter(amqp_connection)

    assert {
        "body": json.loads(dead_letter.body),
        "death_reason": (dead_letter.headers or {}).get("x-first-death-reason"),
        "webhook_calls": webhook.call_count,
    } == {
        "body": {"payment_id": str(payment_id)},
        "death_reason": "rejected",
        "webhook_calls": 3,
    }


async def test_invalid_message_goes_straight_to_dead_letter_queue(
    e2e_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    amqp_connection: AbstractRobustConnection,
) -> None:
    async with (
        processing_service(session_factory) as service,
        running_consumer(e2e_settings, service),
        amqp_connection.channel() as channel,
    ):
        exchange = await channel.get_exchange(PAYMENTS_EXCHANGE_NAME)
        await exchange.publish(aio_pika.Message(body=b"not json"), routing_key=NEW_PAYMENTS_QUEUE_NAME)
        dead_letter = await receive_dead_letter(amqp_connection)

    assert {
        "body": dead_letter.body,
        "death_reason": (dead_letter.headers or {}).get("x-first-death-reason"),
    } == {"body": b"not json", "death_reason": "rejected"}
