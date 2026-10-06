import logging
import uuid

from faststream.rabbit import Channel, ExchangeType, QueueType, RabbitBroker, RabbitExchange, RabbitQueue
from pydantic import BaseModel, ConfigDict

from src.config import Settings

PAYMENTS_EXCHANGE_NAME = "payments"
DEAD_LETTER_EXCHANGE_NAME = "payments.dlx"
NEW_PAYMENTS_QUEUE_NAME = "payments.new"
DEAD_LETTER_QUEUE_NAME = "payments.dlq"

payments_exchange = RabbitExchange(PAYMENTS_EXCHANGE_NAME, type=ExchangeType.DIRECT, durable=True)
dead_letter_exchange = RabbitExchange(DEAD_LETTER_EXCHANGE_NAME, type=ExchangeType.DIRECT, durable=True)
dead_letter_queue = RabbitQueue(DEAD_LETTER_QUEUE_NAME, queue_type=QueueType.QUORUM, routing_key=DEAD_LETTER_QUEUE_NAME)


class PaymentCreatedMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    payment_id: uuid.UUID


def create_broker(settings: Settings) -> RabbitBroker:
    return RabbitBroker(
        settings.rabbitmq_url,
        logger=logging.getLogger("faststream.access.rabbit"),
        default_channel=Channel(
            prefetch_count=settings.consumer_prefetch_count,
            publisher_confirms=True,
            on_return_raises=True,
        ),
    )


def build_new_payments_queue(settings: Settings) -> RabbitQueue:
    return RabbitQueue(
        NEW_PAYMENTS_QUEUE_NAME,
        queue_type=QueueType.QUORUM,
        routing_key=NEW_PAYMENTS_QUEUE_NAME,
        arguments={
            "x-dead-letter-exchange": DEAD_LETTER_EXCHANGE_NAME,
            "x-dead-letter-routing-key": DEAD_LETTER_QUEUE_NAME,
            "x-delivery-limit": settings.queue_delivery_limit,
            "x-max-length": settings.queue_max_length,
            "x-overflow": "reject-publish",
            "x-dead-letter-strategy": "at-least-once",
        },
    )


async def declare_topology(broker: RabbitBroker, settings: Settings) -> None:
    exchange = await broker.declare_exchange(payments_exchange)
    dlx = await broker.declare_exchange(dead_letter_exchange)
    queue = await broker.declare_queue(build_new_payments_queue(settings))
    dlq = await broker.declare_queue(dead_letter_queue)
    await queue.bind(exchange, routing_key=NEW_PAYMENTS_QUEUE_NAME)
    await dlq.bind(dlx, routing_key=DEAD_LETTER_QUEUE_NAME)
