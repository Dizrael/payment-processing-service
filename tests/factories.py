import uuid
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import Settings
from src.db.models import Currency, Payment, PaymentOutbox, PaymentStatus
from src.db.outbox_repository import OutboxRepository
from src.db.payments_repository import NewPayment
from tests.sentinels import DATETIME_NOW, RANDOM, DatetimeNow, Random, coalesce

TEST_API_KEY = "test-api-key"


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "api_key": TEST_API_KEY,
        "database_url": "postgresql+asyncpg://payments:payments@localhost:5432/payments_test",
        "rabbitmq_url": "amqp://guest:guest@localhost:5672/",
        "processing_max_attempts": 3,
        "processing_retry_base_delay_seconds": 0,
        "consumer_prefetch_count": 10,
        "queue_delivery_limit": 3,
        "queue_max_length": 100_000,
        "outbox_poll_interval_seconds": 0.05,
        "outbox_batch_size": 100,
        "webhook_timeout_seconds": 5,
        "webhook_trusted_hosts": frozenset(),
        "gateway_min_delay_seconds": 0,
        "gateway_max_delay_seconds": 0,
        "gateway_success_rate": 1,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def generate_new_payment(
    *,
    idempotency_key: uuid.UUID | Random = RANDOM,
    request_hash: str | Random = RANDOM,
    amount: Decimal | Random = RANDOM,
) -> NewPayment:
    return NewPayment(
        amount=coalesce(amount, RANDOM.decimal),
        currency=RANDOM.choice(list(Currency)),
        description=f"Order #{RANDOM.token()}",
        meta={"order_id": RANDOM.token()},
        webhook_url=f"https://{RANDOM.token()}.merchant.test/webhooks",
        idempotency_key=coalesce(idempotency_key, RANDOM.uuid4),
        request_hash=coalesce(request_hash, lambda: RANDOM.token(32)),
    )


async def generate_payment(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    status: PaymentStatus = PaymentStatus.PENDING,
    new_payment: NewPayment | Random = RANDOM,
) -> Payment:
    async with session_factory() as session:
        payment = Payment(
            id=uuid.uuid4(),
            status=status,
            **asdict(coalesce(new_payment, generate_new_payment)),
        )
        session.add(payment)
        await session.commit()
        return payment


async def generate_outbox_entry(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    next_attempt_at: datetime | DatetimeNow = DATETIME_NOW,
    published_at: datetime | None = None,
) -> PaymentOutbox:
    payment = await generate_payment(session_factory)
    async with session_factory() as session:
        entry = await OutboxRepository(session).add(payment.id)
        if isinstance(next_attempt_at, datetime):
            entry.next_attempt_at = next_attempt_at
        entry.published_at = published_at
        await session.commit()
        return entry
