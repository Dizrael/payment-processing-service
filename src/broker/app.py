import logging

import httpx
from faststream import FastStream

from src.broker.config import create_broker, declare_topology
from src.broker.consumer import register_payment_consumer
from src.config import Settings
from src.db.session import create_engine, create_session_factory
from src.services.gateway import EmulatedGateway
from src.services.processing_service import ProcessingService, RetryPolicy
from src.services.webhook_sender import HttpxWebhookSender


def create_consumer_app(settings: Settings) -> FastStream:
    broker = create_broker(settings)
    engine = create_engine(settings.database_url)
    http_client = httpx.AsyncClient(timeout=settings.webhook_timeout_seconds, trust_env=False)
    processing_service = ProcessingService(
        session_factory=create_session_factory(engine),
        gateway=EmulatedGateway(
            settings.gateway_min_delay_seconds,
            settings.gateway_max_delay_seconds,
            settings.gateway_success_rate,
        ),
        webhook_sender=HttpxWebhookSender(http_client, settings.webhook_trusted_hosts),
        retry_policy=RetryPolicy(
            max_attempts=settings.processing_max_attempts,
            base_delay_seconds=settings.processing_retry_base_delay_seconds,
        ),
    )
    register_payment_consumer(broker, settings, processing_service)
    app = FastStream(broker, logger=logging.getLogger("faststream.app"))

    @app.on_startup
    async def declare_broker_topology() -> None:
        await broker.connect()
        await declare_topology(broker, settings)

    @app.after_shutdown
    async def close_resources() -> None:
        await http_client.aclose()
        await engine.dispose()

    return app
