import json
import uuid

import pytest

from src.broker.consumer import handle_payment_message
from src.services.errors import PaymentNotFoundError


class FakeMessage:
    def __init__(self, body: bytes, message_id: str | None = "message-1") -> None:
        self.body = body
        self.message_id = message_id
        self.acked = False
        self.rejected_with: bool | None = None

    async def ack(self) -> None:
        self.acked = True

    async def reject(self, requeue: bool = False) -> None:
        self.rejected_with = requeue


class FakeProcessor:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[uuid.UUID, str]] = []

    async def process(self, payment_id: uuid.UUID, message_id: str) -> None:
        self.calls.append((payment_id, message_id))
        if self.error is not None:
            raise self.error


def body_for(payment_id: uuid.UUID) -> bytes:
    return json.dumps({"payment_id": str(payment_id)}).encode()


def outcome(message: FakeMessage, processor: FakeProcessor) -> dict[str, object]:
    return {"acked": message.acked, "rejected_with": message.rejected_with, "processed": processor.calls}


async def test_valid_message_is_processed_and_acked() -> None:
    payment_id = uuid.uuid4()
    message, processor = FakeMessage(body_for(payment_id)), FakeProcessor()

    await handle_payment_message(message, processor)

    assert outcome(message, processor) == {
        "acked": True,
        "rejected_with": None,
        "processed": [(payment_id, "message-1")],
    }


@pytest.mark.parametrize(
    "body",
    [b"not json", b"{}", b'{"payment_id": "nope"}', b'{"payment_id": null}', b'{"payment_id": "1", "x": 1}'],
)
async def test_invalid_message_is_rejected_without_requeue(body: bytes) -> None:
    message, processor = FakeMessage(body), FakeProcessor()

    await handle_payment_message(message, processor)

    assert outcome(message, processor) == {"acked": False, "rejected_with": False, "processed": []}


@pytest.mark.parametrize("error", [PaymentNotFoundError(uuid.uuid4()), RuntimeError("retries exhausted")])
async def test_processing_failure_rejects_to_dead_letter(error: Exception) -> None:
    payment_id = uuid.uuid4()
    message, processor = FakeMessage(body_for(payment_id)), FakeProcessor(error)

    await handle_payment_message(message, processor)

    assert outcome(message, processor) == {
        "acked": False,
        "rejected_with": False,
        "processed": [(payment_id, "message-1")],
    }


async def test_missing_message_id_falls_back_to_empty_string() -> None:
    payment_id = uuid.uuid4()
    message, processor = FakeMessage(body_for(payment_id), message_id=None), FakeProcessor()

    await handle_payment_message(message, processor)

    assert outcome(message, processor) == {"acked": True, "rejected_with": None, "processed": [(payment_id, "")]}
