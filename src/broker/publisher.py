import uuid
from collections.abc import Mapping
from typing import Any

from aio_pika.exceptions import DeliveryError
from faststream.rabbit import RabbitBroker

from src.broker.config import NEW_PAYMENTS_QUEUE_NAME, payments_exchange
from src.services.errors import PublishRejectedError


class RabbitPaymentPublisher:
    def __init__(self, broker: RabbitBroker) -> None:
        self._broker = broker

    async def publish(self, payload: Mapping[str, Any], message_id: uuid.UUID) -> None:
        try:
            await self._broker.publish(
                dict(payload),
                exchange=payments_exchange,
                routing_key=NEW_PAYMENTS_QUEUE_NAME,
                message_id=str(message_id),
                persist=True,
            )
        except DeliveryError as error:
            raise PublishRejectedError(message_id) from error
