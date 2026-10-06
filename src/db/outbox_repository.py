import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import PaymentOutbox

MAX_PUBLISH_RETRY_DELAY_SECONDS = 60
MAX_LAST_ERROR_LENGTH = 1000


def publish_retry_delay(attempts: int) -> timedelta:
    return timedelta(seconds=min(2**attempts, MAX_PUBLISH_RETRY_DELAY_SECONDS))


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, payment_id: uuid.UUID) -> PaymentOutbox:
        entry = PaymentOutbox(
            id=uuid.uuid4(),
            payment_id=payment_id,
            payload={"payment_id": str(payment_id)},
            attempts=0,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def claim_ready_to_publish(self, limit: int) -> Sequence[PaymentOutbox]:
        statement = (
            select(PaymentOutbox)
            .where(
                PaymentOutbox.published_at.is_(None),
                PaymentOutbox.next_attempt_at <= func.now(),
            )
            .order_by(PaymentOutbox.next_attempt_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.scalars(statement)
        return result.all()

    def mark_published(self, entry: PaymentOutbox) -> None:
        entry.published_at = datetime.now(UTC)

    def mark_failed(self, entry: PaymentOutbox, error: str) -> None:
        entry.attempts += 1
        entry.last_error = error[:MAX_LAST_ERROR_LENGTH]
        entry.next_attempt_at = datetime.now(UTC) + publish_retry_delay(entry.attempts)
