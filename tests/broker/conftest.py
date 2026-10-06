from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import aio_pika
import pytest
from aio_pika.abc import AbstractRobustConnection
from testcontainers.core.container import DockerContainer
from testcontainers.core.image import DockerImage
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from src.broker.config import (
    DEAD_LETTER_EXCHANGE_NAME,
    DEAD_LETTER_QUEUE_NAME,
    NEW_PAYMENTS_QUEUE_NAME,
    PAYMENTS_EXCHANGE_NAME,
)
from src.config import Settings

RABBITMQ_IMAGE_DIR = Path(__file__).resolve().parent.parent / "rabbitmq"
RABBITMQ_IMAGE_TAG = "payment-processing-test-rabbitmq:local"
AMQP_PORT = 5672
RABBITMQ_STARTUP_TIMEOUT_SECONDS = 90


@pytest.fixture(scope="session")
def rabbitmq_url() -> Iterator[str]:
    with (
        DockerImage(path=str(RABBITMQ_IMAGE_DIR), tag=RABBITMQ_IMAGE_TAG, clean_up=False) as image,
        DockerContainer(str(image))
        .with_exposed_ports(AMQP_PORT)
        .waiting_for(
            LogMessageWaitStrategy("Server startup complete").with_startup_timeout(RABBITMQ_STARTUP_TIMEOUT_SECONDS)
        ) as container,
    ):
        host = container.get_container_host_ip()
        port = container.get_exposed_port(AMQP_PORT)
        yield f"amqp://test:test@{host}:{port}/"


@pytest.fixture
async def amqp_connection(rabbitmq_url: str) -> AsyncIterator[AbstractRobustConnection]:
    connection = await aio_pika.connect_robust(rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        for queue_name in (NEW_PAYMENTS_QUEUE_NAME, DEAD_LETTER_QUEUE_NAME):
            await channel.queue_delete(queue_name)
        for exchange_name in (PAYMENTS_EXCHANGE_NAME, DEAD_LETTER_EXCHANGE_NAME):
            await channel.exchange_delete(exchange_name)
        await channel.close()
        yield connection


@pytest.fixture
def e2e_settings(settings: Settings, rabbitmq_url: str) -> Settings:
    return settings.model_copy(update={"rabbitmq_url": rabbitmq_url})
