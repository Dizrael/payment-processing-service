import uuid

import pytest
from aio_pika.exceptions import DeliveryError
from faststream.rabbit import RabbitMessage, TestRabbitBroker
from pamqp.commands import Basic

from src.broker.config import build_new_payments_queue, create_broker, payments_exchange
from src.broker.publisher import RabbitPaymentPublisher
from src.services.errors import PublishRejectedError
from tests.factories import build_settings


async def test_publish_routes_payload_with_message_id_to_new_payments_queue() -> None:
    settings = build_settings()
    broker = create_broker(settings)
    received: list[dict[str, object]] = []

    @broker.subscriber(build_new_payments_queue(settings), payments_exchange)
    async def capture(body: dict[str, str], message: RabbitMessage) -> None:
        received.append({"body": body, "message_id": message.message_id})

    message_id = uuid.uuid4()
    async with TestRabbitBroker(broker):
        await RabbitPaymentPublisher(broker).publish({"payment_id": "42"}, message_id)

    assert received == [{"body": {"payment_id": "42"}, "message_id": str(message_id)}]


@pytest.mark.parametrize("frame", [Basic.Nack(delivery_tag=1), Basic.Return(reply_code=312, reply_text="NO_ROUTE")])
async def test_broker_delivery_failure_becomes_publish_rejected(
    monkeypatch: pytest.MonkeyPatch, frame: Basic.Nack | Basic.Return
) -> None:
    broker = create_broker(build_settings())

    async def refuse(*_args: object, **_kwargs: object) -> None:
        raise DeliveryError(None, frame)

    monkeypatch.setattr(broker, "publish", refuse)
    message_id = uuid.uuid4()

    with pytest.raises(PublishRejectedError) as raised:
        await RabbitPaymentPublisher(broker).publish({"payment_id": "42"}, message_id)

    assert str(raised.value) == f"Broker rejected message {message_id}"
