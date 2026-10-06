import asyncio

from aio_pika.abc import AbstractIncomingMessage, AbstractRobustConnection

from src.broker.config import DEAD_LETTER_QUEUE_NAME

DEAD_LETTER_WAIT_SECONDS = 15


async def receive_dead_letter(
    connection: AbstractRobustConnection, wait_seconds: float = DEAD_LETTER_WAIT_SECONDS
) -> AbstractIncomingMessage:
    received: asyncio.Future[AbstractIncomingMessage] = asyncio.get_running_loop().create_future()

    async def capture(message: AbstractIncomingMessage) -> None:
        if not received.done():
            received.set_result(message)

    async with connection.channel() as channel:
        queue = await channel.get_queue(DEAD_LETTER_QUEUE_NAME)
        consumer_tag = await queue.consume(capture, no_ack=True)
        try:
            return await asyncio.wait_for(received, wait_seconds)
        finally:
            await queue.cancel(consumer_tag)
