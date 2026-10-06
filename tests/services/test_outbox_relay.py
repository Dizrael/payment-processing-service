import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import PaymentOutbox
from src.services.outbox_relay import OutboxRelay
from tests.factories import generate_outbox_entry
from tests.fakes import FakePublisher
from tests.sentinels import DATETIME_NOW


async def load_entries(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[uuid.UUID, dict[str, Any]]:
    async with session_factory() as session:
        entries = (await session.scalars(select(PaymentOutbox))).all()
    return {entry.id: entry.column_values() for entry in entries}


async def test_relay_batch_publishes_due_entries(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    entry = await generate_outbox_entry(session_factory)
    initial = (await load_entries(session_factory))[entry.id]
    publisher = FakePublisher()

    processed = await OutboxRelay(session_factory, publisher, batch_size=10).relay_batch()

    stored = (await load_entries(session_factory))[entry.id]
    assert {
        "processed": processed,
        "published": publisher.published,
        "entry": stored,
    } == {
        "processed": 1,
        "published": [({"payment_id": str(entry.payment_id)}, entry.id)],
        "entry": {**initial, "published_at": DATETIME_NOW},
    }


async def test_relay_batch_respects_batch_size(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    for _ in range(3):
        await generate_outbox_entry(session_factory)
    publisher = FakePublisher()

    processed = await OutboxRelay(session_factory, publisher, batch_size=2).relay_batch()

    stored = await load_entries(session_factory)
    assert {
        "processed": processed,
        "published_count": len(publisher.published),
        "stored_published": sorted(entry["published_at"] is not None for entry in stored.values()),
    } == {
        "processed": 2,
        "published_count": 2,
        "stored_published": [False, True, True],
    }


async def test_rejected_publish_schedules_retry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    rejected = await generate_outbox_entry(session_factory)
    accepted = await generate_outbox_entry(session_factory)
    initial = await load_entries(session_factory)
    publisher = FakePublisher()
    publisher.reject_ids.add(rejected.id)

    await OutboxRelay(session_factory, publisher, batch_size=10).relay_batch()

    stored = await load_entries(session_factory)
    assert {"rejected": stored[rejected.id], "accepted": stored[accepted.id]} == {
        "rejected": {
            **initial[rejected.id],
            "attempts": 1,
            "last_error": f"Broker rejected message {rejected.id}",
            "next_attempt_at": DATETIME_NOW + timedelta(seconds=2),
        },
        "accepted": {**initial[accepted.id], "published_at": DATETIME_NOW},
    }


async def test_broker_failure_rolls_back_whole_batch(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    entry = await generate_outbox_entry(session_factory)
    initial = (await load_entries(session_factory))[entry.id]
    publisher = FakePublisher()
    publisher.fail_with = ConnectionError("broker is down")

    with pytest.raises(ConnectionError):
        await OutboxRelay(session_factory, publisher, batch_size=10).relay_batch()

    assert (await load_entries(session_factory))[entry.id] == initial


async def test_run_drains_full_batches_without_waiting(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    for _ in range(3):
        await generate_outbox_entry(session_factory)
    publisher = FakePublisher()
    stop_event = asyncio.Event()
    relay = OutboxRelay(session_factory, publisher, batch_size=1)

    task = asyncio.create_task(relay.run(stop_event, poll_interval_seconds=30))
    try:
        async with asyncio.timeout(5):
            await publisher.wait_for_published(3)
    finally:
        stop_event.set()
        await asyncio.wait_for(task, timeout=5)

    assert len(publisher.published) == 3


async def test_run_survives_iteration_errors_and_stops_on_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    entry = await generate_outbox_entry(session_factory)
    publisher = FakePublisher()
    publisher.fail_with = ConnectionError("broker is down")
    stop_event = asyncio.Event()
    relay = OutboxRelay(session_factory, publisher, batch_size=10)

    task = asyncio.create_task(relay.run(stop_event, poll_interval_seconds=0.01))
    await asyncio.sleep(0.05)
    publisher.fail_with = None
    async with asyncio.timeout(5):
        await publisher.wait_for_published(1)
    stop_event.set()

    await asyncio.wait_for(task, timeout=1)
    assert {"done": task.done(), "published": publisher.published} == {
        "done": True,
        "published": [({"payment_id": str(entry.payment_id)}, entry.id)],
    }
