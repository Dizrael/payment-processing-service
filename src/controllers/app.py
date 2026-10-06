from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncEngine

from src.config import Settings
from src.controllers import health, payments
from src.db.session import create_session_factory


def route_name(route: APIRoute) -> str:
    return route.name


def create_api(settings: Settings, engine: AsyncEngine) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(
        title="Payment Processing Service",
        version="2026.1",
        lifespan=lifespan,
        generate_unique_id_function=route_name,
    )
    app.state.settings = settings
    app.state.session_factory = create_session_factory(engine)
    app.include_router(payments.router)
    app.include_router(health.router)
    return app
