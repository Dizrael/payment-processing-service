import re
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import Currency, Payment, PaymentOutbox, PaymentStatus
from src.schemas import PaymentCreateRequest
from src.services.errors import IdempotencyConflictError, PaymentNotFoundError
from src.services.payment_service import PaymentService, compute_request_hash


def make_request(**overrides: object) -> PaymentCreateRequest:
    body: dict[str, object] = {
        "amount": "250.00",
        "currency": "USD",
        "description": "Subscription",
        "metadata": {"plan": "pro"},
        "webhook_url": "https://merchant.test/webhooks",
    }
    body.update(overrides)
    return PaymentCreateRequest.model_validate(body)


async def count_rows(
    session_factory: async_sessionmaker[AsyncSession], model: type[Payment] | type[PaymentOutbox]
) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(model)) or 0


def test_request_hash_is_stable_for_equivalent_amounts() -> None:
    assert compute_request_hash(make_request(amount="250")) == compute_request_hash(make_request(amount="250.00"))


def test_request_hash_differs_for_different_bodies() -> None:
    assert compute_request_hash(make_request()) != compute_request_hash(make_request(description="Other"))


def test_request_hash_is_sha256_hex() -> None:
    request_hash = compute_request_hash(make_request())

    assert re.fullmatch(r"[0-9a-f]{64}", request_hash) is not None


async def test_create_stores_payment_and_outbox_entry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    key = uuid.uuid4()
    request = make_request()

    async with session_factory() as session:
        payment = await PaymentService(session).create(request, key)

    async with session_factory() as session:
        stored = await session.get(Payment, payment.id)
        outbox = (await session.scalars(select(PaymentOutbox))).all()

    assert stored is not None
    stored_values = stored.column_values()
    outbox_values = [entry.column_values() for entry in outbox]
    assert stored_values == {
        "id": payment.id,
        "amount": request.amount,
        "currency": Currency.USD,
        "description": "Subscription",
        "meta": {"plan": "pro"},
        "status": PaymentStatus.PENDING,
        "failure_reason": None,
        "idempotency_key": key,
        "request_hash": compute_request_hash(request),
        "webhook_url": "https://merchant.test/webhooks",
        "created_at": stored_values["created_at"],
        "processed_at": None,
    }
    assert outbox_values == [
        {
            "id": outbox_values[0]["id"],
            "payment_id": payment.id,
            "payload": {"payment_id": str(payment.id)},
            "attempts": 0,
            "next_attempt_at": outbox_values[0]["next_attempt_at"],
            "last_error": None,
            "created_at": outbox_values[0]["created_at"],
            "published_at": None,
        }
    ]


async def test_create_with_same_key_and_body_returns_existing_payment(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    key = uuid.uuid4()

    async with session_factory() as session:
        first = await PaymentService(session).create(make_request(), key)
    async with session_factory() as session:
        second = await PaymentService(session).create(make_request(), key)

    assert second.column_values() == first.column_values()
    assert (await count_rows(session_factory, Payment), await count_rows(session_factory, PaymentOutbox)) == (1, 1)


async def test_create_with_same_key_and_other_body_raises_conflict(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    key = uuid.uuid4()

    async with session_factory() as session:
        await PaymentService(session).create(make_request(), key)

    async with session_factory() as session:
        with pytest.raises(IdempotencyConflictError):
            await PaymentService(session).create(make_request(amount="1.00"), key)

    assert await count_rows(session_factory, Payment) == 1


async def test_get_returns_payment(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        created = await PaymentService(session).create(make_request(), uuid.uuid4())

    async with session_factory() as session:
        found = await PaymentService(session).get(created.id)

    assert found.column_values() == created.column_values()


async def test_get_unknown_payment_raises_not_found(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        with pytest.raises(PaymentNotFoundError):
            await PaymentService(session).get(uuid.uuid4())
