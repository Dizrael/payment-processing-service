import logging
import uuid
from typing import Protocol

from faststream import AckPolicy
from faststream.rabbit import RabbitBroker, RabbitMessage
from pydantic import ValidationError

from src.broker.config import PaymentCreatedMessage, build_new_payments_queue, payments_exchange
from src.config import Settings
from src.logger import bind_log_context

logger = logging.getLogger(__name__)


class PaymentProcessor(Protocol):
    async def process(self, payment_id: uuid.UUID, message_id: str) -> None: ...


class AcknowledgeableMessage(Protocol):
    @property
    def body(self) -> bytes: ...

    @property
    def message_id(self) -> str | None: ...

    async def ack(self) -> None: ...

    async def reject(self, requeue: bool) -> None: ...


async def handle_payment_message(message: AcknowledgeableMessage, processor: PaymentProcessor) -> None:
    message_id = message.message_id or ""
    with bind_log_context(message_id=message_id):
        try:
            payload = PaymentCreatedMessage.model_validate_json(message.body)
        except ValidationError:
            logger.warning("Invalid message payload, moving it to the dead letter queue")
            await message.reject(requeue=False)
            return
        with bind_log_context(payment_id=str(payload.payment_id)):
            try:
                await processor.process(payload.payment_id, message_id)
            except Exception:
                logger.exception("Payment processing failed, moving message to the dead letter queue")
                await message.reject(requeue=False)
                return
            await message.ack()
            logger.info("Payment message acknowledged")


def register_payment_consumer(broker: RabbitBroker, settings: Settings, processor: PaymentProcessor) -> None:
    @broker.subscriber(build_new_payments_queue(settings), payments_exchange, ack_policy=AckPolicy.MANUAL)
    async def consume_new_payment(message: RabbitMessage) -> None:
        await handle_payment_message(message, processor)
