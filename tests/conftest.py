from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.config import Settings
from src.controllers.app import create_api
from src.db.session import create_engine, create_session_factory
from tests.database import temporary_database, upgrade_database, with_database_suffix
from tests.factories import build_settings


class PytestEnvironment(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    test_database_url: str = "postgresql+asyncpg://payments:payments@localhost:5432/payments_test"


@pytest.fixture(scope="session")
def database_url(worker_id: str) -> str:
    base_url = PytestEnvironment().test_database_url
    if worker_id == "master":
        return base_url
    return with_database_suffix(base_url, worker_id)


@pytest.fixture(scope="session")
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    async with temporary_database(database_url):
        await upgrade_database(database_url)
        test_engine = create_engine(database_url)
        yield test_engine
        await test_engine.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE payments, payments_outbox CASCADE"))
    return create_session_factory(engine)


@pytest.fixture
def settings(database_url: str) -> Settings:
    return build_settings(database_url=database_url)


@pytest.fixture
async def client(
    settings: Settings,
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_api(settings, engine)
    app.state.session_factory = session_factory
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-API-Key": settings.api_key.get_secret_value()},
    ) as test_client:
        yield test_client
