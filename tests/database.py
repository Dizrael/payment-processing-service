import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def with_database_suffix(database_url: str, suffix: str) -> str:
    url = make_url(database_url)
    return url.set(database=f"{url.database}_{suffix}").render_as_string(hide_password=False)


async def run_admin_statements(database_url: str, *statements: str) -> None:
    url: URL = make_url(database_url)
    admin_engine = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as connection:
            for statement in statements:
                await connection.execute(text(statement.format(database=url.database)))
    finally:
        await admin_engine.dispose()


async def drop_database(database_url: str) -> None:
    await run_admin_statements(database_url, 'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')


async def recreate_database(database_url: str) -> None:
    await drop_database(database_url)
    await run_admin_statements(database_url, 'CREATE DATABASE "{database}"')


@asynccontextmanager
async def temporary_database(database_url: str) -> AsyncIterator[None]:
    await recreate_database(database_url)
    try:
        yield
    finally:
        await drop_database(database_url)


def alembic_config(database_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


async def upgrade_database(database_url: str, revision: str = "head") -> None:
    await asyncio.to_thread(command.upgrade, alembic_config(database_url), revision)


async def downgrade_database(database_url: str, revision: str = "base") -> None:
    await asyncio.to_thread(command.downgrade, alembic_config(database_url), revision)
