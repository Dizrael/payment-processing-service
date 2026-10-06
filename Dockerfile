FROM python:3.13.16-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /srv

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project


FROM python:3.13.16-slim AS runtime

RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --no-create-home app

WORKDIR /srv

COPY --from=builder --chown=app:app /srv/.venv /srv/.venv
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app db_migrations ./db_migrations
COPY --chown=app:app src ./src
COPY --chown=app:app webhook_mock ./webhook_mock
COPY --chown=app:app order_generator ./order_generator

ENV PATH="/srv/.venv/bin:$PATH" \
    PYTHONPATH=/srv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app
