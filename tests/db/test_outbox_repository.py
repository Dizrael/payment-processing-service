import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import PaymentOutbox
from src.db.outbox_repository import OutboxRepository, publish_retry_delay
from tests.factories import generate_outbox_entry
from tests.sentinels import DATETIME_NOW


async def load_entry(session_factory: async_sessionmaker[AsyncSession], entry_id: uuid.UUID) -> dict[str, Any]:
    async with session_factory() as session:
        stored = await session.scalar(select(PaymentOutbox).where(PaymentOutbox.id == entry_id))
    assert stored is not None
    return stored.column_values()


async def test_add_creates_entry_with_payload(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    created = await generate_outbox_entry(session_factory)
    stored = await load_entry(session_factory, created.id)

    assert stored == {
        "id": created.id,
        "payment_id": created.payment_id,
        "payload": {"payment_id": str(created.payment_id)},
        "attempts": 0,
        "next_attempt_at": DATETIME_NOW,
        "last_error": None,
        "created_at": DATETIME_NOW,
        "published_at": None,
    }


async def test_claim_ready_to_publish_skips_published_and_future_entries(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    due = await generate_outbox_entry(session_factory)
    await generate_outbox_entry(session_factory, published_at=now)
    await generate_outbox_entry(session_factory, next_attempt_at=now + timedelta(hours=1))

    async with session_factory() as session:
        batch = await OutboxRepository(session).claim_ready_to_publish(limit=10)

    assert [entry.id for entry in batch] == [due.id]


async def test_claim_ready_to_publish_orders_by_next_attempt_and_limits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    await generate_outbox_entry(session_factory, next_attempt_at=now - timedelta(seconds=1))
    oldest = await generate_outbox_entry(session_factory, next_attempt_at=now - timedelta(seconds=3))
    middle = await generate_outbox_entry(session_factory, next_attempt_at=now - timedelta(seconds=2))

    async with session_factory() as session:
        await session.execute(text("SET LOCAL enable_indexscan = off"))
        await session.execute(text("SET LOCAL enable_bitmapscan = off"))
        batch = await OutboxRepository(session).claim_ready_to_publish(limit=2)

    assert [entry.id for entry in batch] == [oldest.id, middle.id]


async def test_claim_ready_to_publish_skips_rows_locked_by_another_transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    first = await generate_outbox_entry(session_factory, next_attempt_at=now - timedelta(seconds=2))
    second = await generate_outbox_entry(session_factory, next_attempt_at=now - timedelta(seconds=1))

    async with session_factory() as relay_a, session_factory() as relay_b:
        batch_a = await OutboxRepository(relay_a).claim_ready_to_publish(limit=1)
        batch_b = await OutboxRepository(relay_b).claim_ready_to_publish(limit=10)

    assert [[entry.id for entry in batch_a], [entry.id for entry in batch_b]] == [[first.id], [second.id]]


async def test_mark_published_sets_timestamp(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    created = await generate_outbox_entry(session_factory)

    async with session_factory() as session:
        repository = OutboxRepository(session)
        [entry] = await repository.claim_ready_to_publish(limit=1)
        repository.mark_published(entry)
        await session.commit()

    stored = await load_entry(session_factory, created.id)

    assert stored == {
        "id": created.id,
        "payment_id": created.payment_id,
        "payload": {"payment_id": str(created.payment_id)},
        "attempts": 0,
        "next_attempt_at": DATETIME_NOW,
        "last_error": None,
        "created_at": DATETIME_NOW,
        "published_at": DATETIME_NOW,
    }


async def test_mark_failed_increments_attempts_and_delays_next_attempt(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    created = await generate_outbox_entry(session_factory)

    async with session_factory() as session:
        repository = OutboxRepository(session)
        [entry] = await repository.claim_ready_to_publish(limit=1)
        repository.mark_failed(entry, "queue is full")
        await session.commit()

    stored = await load_entry(session_factory, created.id)

    assert stored == {
        "id": created.id,
        "payment_id": created.payment_id,
        "payload": {"payment_id": str(created.payment_id)},
        "attempts": 1,
        "next_attempt_at": DATETIME_NOW + timedelta(seconds=2),
        "last_error": "queue is full",
        "created_at": DATETIME_NOW,
        "published_at": None,
    }


@pytest.mark.parametrize(
    ("attempts", "expected_seconds"),
    [(1, 2), (2, 4), (3, 8), (5, 32), (6, 60), (20, 60)],
)
def test_publish_retry_delay_is_exponential_and_capped(attempts: int, expected_seconds: int) -> None:
    assert publish_retry_delay(attempts) == timedelta(seconds=expected_seconds)
