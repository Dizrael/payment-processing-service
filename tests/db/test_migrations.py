from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.db.base import Base
from tests.database import (
    downgrade_database,
    temporary_database,
    upgrade_database,
    with_database_suffix,
)


async def table_names(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            names = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    finally:
        await engine.dispose()
    return set(names) - {"alembic_version"}


async def test_migrations_upgrade_downgrade_roundtrip(database_url: str) -> None:
    migrations_url = with_database_suffix(database_url, "migrations")
    async with temporary_database(migrations_url):
        await upgrade_database(migrations_url)
        after_upgrade = await table_names(migrations_url)
        await downgrade_database(migrations_url)
        after_downgrade = await table_names(migrations_url)
        await upgrade_database(migrations_url)
        after_second_upgrade = await table_names(migrations_url)

    assert {
        "after_upgrade": after_upgrade,
        "after_downgrade": after_downgrade,
        "after_second_upgrade": after_second_upgrade,
    } == {
        "after_upgrade": {"payments", "payments_outbox"},
        "after_downgrade": set(),
        "after_second_upgrade": {"payments", "payments_outbox"},
    }


def collect_diff(connection: Connection) -> list[Any]:
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return list(compare_metadata(context, Base.metadata))


async def test_models_match_latest_migration(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        diff = await connection.run_sync(collect_diff)

    assert diff == []


async def test_outbox_has_partial_index_on_unpublished(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        indexes = await connection.run_sync(lambda sync: inspect(sync).get_indexes("payments_outbox"))

    actual = [
        {
            "name": index["name"],
            "column_names": index["column_names"],
            "where": str(index["dialect_options"]["postgresql_where"]),
        }
        for index in indexes
        if index["name"] == "ix_payments_outbox_unpublished"
    ]
    assert actual == [
        {
            "name": "ix_payments_outbox_unpublished",
            "column_names": ["next_attempt_at"],
            "where": "(published_at IS NULL)",
        }
    ]
