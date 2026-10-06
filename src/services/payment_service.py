import hashlib
import json
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Payment
from src.db.outbox_repository import OutboxRepository
from src.db.payments_repository import NewPayment, PaymentsRepository
from src.schemas import PaymentCreateRequest
from src.services.errors import IdempotencyConflictError, PaymentNotFoundError

logger = logging.getLogger(__name__)


def compute_request_hash(request: PaymentCreateRequest) -> str:
    canonical = json.dumps(
        request.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._payments = PaymentsRepository(session)
        self._outbox = OutboxRepository(session)

    async def create(self, request: PaymentCreateRequest, idempotency_key: uuid.UUID) -> Payment:
        request_hash = compute_request_hash(request)
        created = await self._payments.insert_if_absent(
            NewPayment(
                **request.model_dump(exclude={"webhook_url"}),
                webhook_url=str(request.webhook_url),
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        )
        if created is not None:
            await self._outbox.add(created.id)
            await self._session.commit()
            logger.info(
                "Payment accepted",
                extra={
                    "payment_id": str(created.id),
                    "idempotency_key": str(idempotency_key),
                    "amount": str(created.amount),
                    "currency": created.currency.value,
                },
            )
            return created

        existing = await self._payments.get_by_idempotency_key(idempotency_key)
        if existing.request_hash != request_hash:
            logger.warning(
                "Idempotency key reused with a different body",
                extra={"payment_id": str(existing.id), "idempotency_key": str(idempotency_key)},
            )
            raise IdempotencyConflictError
        logger.info(
            "Idempotent replay, returning existing payment",
            extra={"payment_id": str(existing.id), "idempotency_key": str(idempotency_key)},
        )
        return existing

    async def get(self, payment_id: uuid.UUID) -> Payment:
        payment = await self._payments.find(payment_id)
        if payment is None:
            raise PaymentNotFoundError(payment_id)
        return payment
