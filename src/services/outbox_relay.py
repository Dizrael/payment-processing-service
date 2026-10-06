import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.outbox_repository import OutboxRepository
from src.services.errors import PublishRejectedError

logger = logging.getLogger(__name__)


class MessagePublisher(Protocol):
    async def publish(self, payload: Mapping[str, Any], message_id: uuid.UUID) -> None: ...


class OutboxRelay:
    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        publisher: MessagePublisher,
        batch_size: int,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._batch_size = batch_size

    async def relay_batch(self) -> int:
        async with self._session_factory() as session:
            outbox = OutboxRepository(session)
            entries = await outbox.claim_ready_to_publish(self._batch_size)
            for entry in entries:
                try:
                    await self._publisher.publish(entry.payload, entry.id)
                except PublishRejectedError as error:
                    logger.warning(
                        "Outbox entry rejected by broker",
                        extra={"outbox_id": str(entry.id), "payment_id": str(entry.payment_id), "error": str(error)},
                    )
                    outbox.mark_failed(entry, str(error))
                else:
                    outbox.mark_published(entry)
                    logger.info(
                        "Outbox entry published",
                        extra={"outbox_id": str(entry.id), "payment_id": str(entry.payment_id)},
                    )
            await session.commit()
            return len(entries)

    async def run(self, stop_event: asyncio.Event, poll_interval_seconds: float) -> None:
        while not stop_event.is_set():
            try:
                processed = await self.relay_batch()
            except Exception:
                logger.exception("Outbox relay iteration failed")
                processed = 0
            if processed >= self._batch_size:
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), poll_interval_seconds)
