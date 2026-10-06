import asyncio
import logging
import signal

from src.broker.app import create_consumer_app
from src.broker.config import create_broker, declare_topology
from src.broker.publisher import RabbitPaymentPublisher
from src.config import Settings, get_settings
from src.controllers.app import create_api
from src.db.session import create_engine, create_session_factory
from src.logger import configure_logging
from src.services.outbox_relay import OutboxRelay

SHUTDOWN_SIGNALS = (signal.SIGINT, signal.SIGTERM)

logger = logging.getLogger(__name__)

settings = get_settings()
configure_logging(settings.log_level)
api = create_api(settings, create_engine(settings.database_url))
consumer_app = create_consumer_app(settings)


async def run_relay(relay_settings: Settings) -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in SHUTDOWN_SIGNALS:
        loop.add_signal_handler(signal_number, stop_event.set)

    engine = create_engine(relay_settings.database_url)
    broker = create_broker(relay_settings)
    try:
        await broker.connect()
        await declare_topology(broker, relay_settings)
        relay = OutboxRelay(
            create_session_factory(engine),
            RabbitPaymentPublisher(broker),
            relay_settings.outbox_batch_size,
        )
        logger.info("Outbox relay started")
        await relay.run(stop_event, relay_settings.outbox_poll_interval_seconds)
    finally:
        for signal_number in SHUTDOWN_SIGNALS:
            loop.remove_signal_handler(signal_number)
        await broker.stop()
        await engine.dispose()
        logger.info("Outbox relay stopped")


if __name__ == "__main__":
    asyncio.run(run_relay(settings))
