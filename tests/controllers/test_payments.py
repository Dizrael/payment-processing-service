import uuid
from datetime import datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import PaymentOutbox, PaymentStatus
from tests.factories import generate_payment
from tests.sentinels import DATETIME_NOW

BODY: dict[str, object] = {
    "amount": "100.5",
    "currency": "RUB",
    "description": "Order #42",
    "metadata": {"order_id": 42},
    "webhook_url": "https://merchant.test/webhooks",
}


def idempotency_headers() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def is_aware_iso_datetime(value: object) -> bool:
    return isinstance(value, str) and datetime.fromisoformat(value).tzinfo is not None


async def test_create_payment_returns_202(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/payments", json=BODY, headers=idempotency_headers())

    body = response.json()
    assert {
        "status": response.status_code,
        "location": response.headers.get("Location"),
        "body": body,
        "created_at_is_aware": is_aware_iso_datetime(body["created_at"]),
    } == {
        "status": 202,
        "location": f"/api/v1/payments/{body['id']}",
        "body": {
            "id": str(uuid.UUID(body["id"])),
            "status": "pending",
            "created_at": DATETIME_NOW,
        },
        "created_at_is_aware": True,
    }


async def test_create_payment_is_idempotent(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    headers = idempotency_headers()

    first = await client.post("/api/v1/payments", json=BODY, headers=headers)
    second = await client.post("/api/v1/payments", json={**BODY, "amount": "100.50"}, headers=headers)

    async with session_factory() as session:
        outbox_payment_ids = list(await session.scalars(select(PaymentOutbox.payment_id)))
    assert {
        "first": {"status": first.status_code, "body": first.json()},
        "second": {"status": second.status_code, "body": second.json()},
        "outbox_payment_ids": outbox_payment_ids,
    } == {
        "first": {"status": 202, "body": first.json()},
        "second": {"status": 202, "body": first.json()},
        "outbox_payment_ids": [uuid.UUID(first.json()["id"])],
    }


async def test_create_payment_with_reused_key_and_other_body_returns_409(
    client: httpx.AsyncClient,
) -> None:
    headers = idempotency_headers()
    await client.post("/api/v1/payments", json=BODY, headers=headers)

    response = await client.post("/api/v1/payments", json={**BODY, "amount": "1.00"}, headers=headers)

    assert {"status": response.status_code, "body": response.json()} == {
        "status": 409,
        "body": {"detail": "Idempotency-Key was already used with a different request body"},
    }


@pytest.mark.parametrize("headers", [{}, {"Idempotency-Key": "not-a-uuid"}])
async def test_create_payment_requires_valid_idempotency_key(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.post("/api/v1/payments", json=BODY, headers=headers)

    assert {"status": response.status_code, "error_location": response.json()["detail"][0]["loc"]} == {
        "status": 422,
        "error_location": ["header", "Idempotency-Key"],
    }


async def test_create_payment_validates_body(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/payments", json={**BODY, "currency": "GBP"}, headers=idempotency_headers())

    assert {"status": response.status_code, "error_location": response.json()["detail"][0]["loc"]} == {
        "status": 422,
        "error_location": ["body", "currency"],
    }


@pytest.mark.parametrize("api_key", [None, "wrong-key"])
async def test_payments_require_valid_api_key(client: httpx.AsyncClient, api_key: str | None) -> None:
    headers = idempotency_headers()
    if api_key is None:
        client.headers.pop("X-API-Key")
    else:
        headers["X-API-Key"] = api_key

    post_response = await client.post("/api/v1/payments", json=BODY, headers=headers)
    get_response = await client.get(f"/api/v1/payments/{uuid.uuid4()}", headers=headers)

    unauthorized = {"status": 401, "body": {"detail": "Invalid or missing API key"}}
    assert {
        "post": {"status": post_response.status_code, "body": post_response.json()},
        "get": {"status": get_response.status_code, "body": get_response.json()},
    } == {"post": unauthorized, "get": unauthorized}


async def test_get_payment_returns_details(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    payment = await generate_payment(session_factory, status=PaymentStatus.SUCCEEDED)

    response = await client.get(f"/api/v1/payments/{payment.id}")

    body = response.json()
    assert {"status": response.status_code, "body": body} == {
        "status": 200,
        "body": {
            "id": str(payment.id),
            "amount": str(payment.amount),
            "currency": payment.currency.value,
            "description": payment.description,
            "metadata": payment.meta,
            "status": "succeeded",
            "failure_reason": None,
            "idempotency_key": str(payment.idempotency_key),
            "webhook_url": payment.webhook_url,
            "created_at": DATETIME_NOW,
            "processed_at": None,
        },
    }


async def test_get_unknown_payment_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/v1/payments/{uuid.uuid4()}")

    assert {"status": response.status_code, "body": response.json()} == {
        "status": 404,
        "body": {"detail": "Payment not found"},
    }


async def test_get_payment_with_invalid_id_returns_422(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/payments/not-a-uuid")

    assert {"status": response.status_code, "error_location": response.json()["detail"][0]["loc"]} == {
        "status": 422,
        "error_location": ["path", "payment_id"],
    }
