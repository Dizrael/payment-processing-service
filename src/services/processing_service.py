import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.db.models import Payment, PaymentStatus
from src.db.payments_repository import PaymentsRepository
from src.services.errors import PaymentNotFoundError
from src.services.gateway import PaymentGateway
from src.services.webhook_sender import WebhookNotification, WebhookSender

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    base_delay_seconds: float
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


def build_notification(payment: Payment) -> WebhookNotification:
    return WebhookNotification(
        payment_id=payment.id,
        status=payment.status,
        amount=payment.amount,
        currency=payment.currency,
        failure_reason=payment.failure_reason,
        processed_at=payment.processed_at,
    )


def log_retry(retry_state: RetryCallState) -> None:
    error = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "Processing attempt failed, retrying",
        extra={"attempt": retry_state.attempt_number, "error": repr(error)},
    )


class ProcessingService:
    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        gateway: PaymentGateway,
        webhook_sender: WebhookSender,
        retry_policy: RetryPolicy,
    ) -> None:
        self._session_factory = session_factory
        self._gateway = gateway
        self._webhook_sender = webhook_sender
        self._retry_policy = retry_policy

    async def process(self, payment_id: uuid.UUID, message_id: str) -> None:
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._retry_policy.max_attempts),
            wait=wait_exponential(multiplier=self._retry_policy.base_delay_seconds),
            retry=retry_if_not_exception_type(PaymentNotFoundError),
            sleep=self._retry_policy.sleep,
            before_sleep=log_retry,
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                await self._process_once(payment_id, message_id)

    async def _process_once(self, payment_id: uuid.UUID, message_id: str) -> None:
        async with self._session_factory() as session:
            payments = PaymentsRepository(session)
            payment = await payments.find_for_update(payment_id)
            if payment is None:
                raise PaymentNotFoundError(payment_id)
            if payment.status is PaymentStatus.PENDING:
                result = await self._gateway.charge(payment.id, payment.amount, payment.currency)
                status = PaymentStatus.SUCCEEDED if result.succeeded else PaymentStatus.FAILED
                await payments.mark_processed(payment, status, result.failure_reason)
                await session.commit()
                logger.info(
                    "Payment processed",
                    extra={"status": status.value, "failure_reason": result.failure_reason},
                )
            else:
                logger.info("Payment already processed, skipping charge", extra={"status": payment.status.value})
            notification = build_notification(payment)
            webhook_url = payment.webhook_url
        await self._webhook_sender.send(webhook_url, notification, message_id)
        logger.info("Webhook delivered", extra={"webhook_url": webhook_url})
