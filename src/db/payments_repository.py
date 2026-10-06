import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Currency, Payment, PaymentStatus


@dataclass(frozen=True, slots=True)
class NewPayment:
    amount: Decimal
    currency: Currency
    description: str
    meta: dict[str, Any]
    webhook_url: str
    idempotency_key: uuid.UUID
    request_hash: str


class PaymentsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_if_absent(self, new_payment: NewPayment) -> Payment | None:
        statement = (
            insert(Payment)
            .values(
                {
                    Payment.id: uuid.uuid4(),
                    Payment.amount: new_payment.amount,
                    Payment.currency: new_payment.currency,
                    Payment.description: new_payment.description,
                    Payment.meta: new_payment.meta,
                    Payment.webhook_url: new_payment.webhook_url,
                    Payment.idempotency_key: new_payment.idempotency_key,
                    Payment.request_hash: new_payment.request_hash,
                }
            )
            .on_conflict_do_nothing(index_elements=[Payment.idempotency_key])
            .returning(Payment)
        )
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def find(self, payment_id: uuid.UUID) -> Payment | None:
        return await self._session.get(Payment, payment_id)

    async def get_by_idempotency_key(self, idempotency_key: uuid.UUID) -> Payment:
        statement = select(Payment).where(Payment.idempotency_key == idempotency_key)
        result = await self._session.scalars(statement)
        return result.one()

    async def find_for_update(self, payment_id: uuid.UUID) -> Payment | None:
        statement = (
            select(Payment).where(Payment.id == payment_id).with_for_update().execution_options(populate_existing=True)
        )
        return await self._session.scalar(statement)

    async def mark_processed(self, payment: Payment, status: PaymentStatus, failure_reason: str | None) -> None:
        payment.status = status
        payment.failure_reason = failure_reason
        payment.processed_at = datetime.now(UTC)
        await self._session.flush()
