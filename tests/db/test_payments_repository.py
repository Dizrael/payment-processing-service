import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import Payment, PaymentStatus
from src.db.payments_repository import PaymentsRepository
from tests.factories import generate_new_payment, generate_payment
from tests.sentinels import DATETIME_NOW


async def test_insert_if_absent_creates_pending_payment(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    new_payment = generate_new_payment()

    async with session_factory() as session:
        payment = await PaymentsRepository(session).insert_if_absent(new_payment)
        await session.commit()

    assert payment is not None
    assert payment.column_values() == {
        "id": payment.id,
        "amount": new_payment.amount,
        "currency": new_payment.currency,
        "description": new_payment.description,
        "meta": new_payment.meta,
        "status": PaymentStatus.PENDING,
        "failure_reason": None,
        "idempotency_key": new_payment.idempotency_key,
        "request_hash": new_payment.request_hash,
        "webhook_url": new_payment.webhook_url,
        "created_at": DATETIME_NOW,
        "processed_at": None,
    }


async def test_insert_if_absent_returns_none_on_duplicate_key(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    key = uuid.uuid4()
    async with session_factory() as session:
        repository = PaymentsRepository(session)
        first = await repository.insert_if_absent(generate_new_payment(idempotency_key=key))
        second = await repository.insert_if_absent(generate_new_payment(idempotency_key=key))
        await session.commit()
        count = await session.scalar(select(func.count()).select_from(Payment))

    assert {"first_inserted": first is not None, "second": second, "count": count} == {
        "first_inserted": True,
        "second": None,
        "count": 1,
    }


async def test_get_by_idempotency_key(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    key = uuid.uuid4()
    async with session_factory() as session:
        created = await PaymentsRepository(session).insert_if_absent(generate_new_payment(idempotency_key=key))
        await session.commit()
    assert created is not None

    async with session_factory() as session:
        found = await PaymentsRepository(session).get_by_idempotency_key(key)

    assert found.column_values() == created.column_values()


async def test_find_returns_none_for_unknown_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await PaymentsRepository(session).find(uuid.uuid4()) is None


async def test_find_for_update_locks_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payment = await generate_payment(session_factory)

    async with session_factory() as locking_session, session_factory() as other_session:
        locked = await PaymentsRepository(locking_session).find_for_update(payment.id)
        assert locked is not None
        with pytest.raises(DBAPIError):
            await other_session.execute(select(Payment).where(Payment.id == payment.id).with_for_update(nowait=True))


async def test_find_for_update_returns_none_for_unknown_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await PaymentsRepository(session).find_for_update(uuid.uuid4()) is None


async def test_mark_processed_sets_status_reason_and_time(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payment = await generate_payment(session_factory)
    before = payment.column_values()

    async with session_factory() as session:
        repository = PaymentsRepository(session)
        locked = await repository.find_for_update(payment.id)
        assert locked is not None
        await repository.mark_processed(locked, PaymentStatus.FAILED, "Declined by gateway")
        await session.commit()

    async with session_factory() as session:
        stored = await PaymentsRepository(session).find(payment.id)

    assert stored is not None
    assert stored.column_values() == {
        **before,
        "status": PaymentStatus.FAILED,
        "failure_reason": "Declined by gateway",
        "processed_at": DATETIME_NOW,
    }
