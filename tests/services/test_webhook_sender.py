import ipaddress
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import respx

from src.db.models import Currency, PaymentStatus
from src.services.errors import WebhookDeliveryError
from src.services.webhook_sender import (
    HttpxWebhookSender,
    Resolver,
    WebhookNotification,
    resolve_host,
)

WEBHOOK_URL = "https://merchant.test/webhooks"
PUBLIC_ADDRESS = "93.184.215.14"
PINNED_URL = f"https://{PUBLIC_ADDRESS}/webhooks"
UNSPECIFIED_ADDRESS = str(ipaddress.IPv4Address(0))


def resolver_returning(*addresses: str) -> Resolver:
    async def resolve(_host: str) -> list[str]:
        return list(addresses)

    return resolve


async def failing_resolver(host: str) -> list[str]:
    msg = f"Name or service not known: {host}"
    raise OSError(msg)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=1) as http_client:
        yield http_client


@pytest.fixture
def sender(client: httpx.AsyncClient) -> HttpxWebhookSender:
    return HttpxWebhookSender(client, trusted_hosts=frozenset(), resolver=resolver_returning(PUBLIC_ADDRESS))


def make_notification() -> WebhookNotification:
    return WebhookNotification(
        payment_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        status=PaymentStatus.FAILED,
        amount=Decimal("100.00"),
        currency=Currency.RUB,
        failure_reason="Declined by gateway",
        processed_at=datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
    )


@respx.mock
async def test_send_posts_to_pinned_address_with_original_host(
    sender: HttpxWebhookSender,
) -> None:
    route = respx.post(PINNED_URL).mock(return_value=httpx.Response(200))

    await sender.send(WEBHOOK_URL, make_notification(), "message-1")

    request = route.calls.last.request
    assert {
        "url": str(request.url),
        "host": request.headers.get("Host"),
        "sni": request.extensions.get("sni_hostname"),
        "message_id": request.headers.get("X-Message-Id"),
        "body": json.loads(request.content),
    } == {
        "url": PINNED_URL,
        "host": "merchant.test",
        "sni": "merchant.test",
        "message_id": "message-1",
        "body": {
            "payment_id": "11111111-1111-1111-1111-111111111111",
            "status": "failed",
            "amount": "100.00",
            "currency": "RUB",
            "failure_reason": "Declined by gateway",
            "processed_at": "2026-10-05T12:00:00+00:00",
        },
    }


@respx.mock
async def test_send_keeps_non_default_port_in_host_header(client: httpx.AsyncClient) -> None:
    route = respx.post(f"https://{PUBLIC_ADDRESS}:8443/webhooks").mock(return_value=httpx.Response(200))
    sender = HttpxWebhookSender(client, trusted_hosts=frozenset(), resolver=resolver_returning(PUBLIC_ADDRESS))

    await sender.send("https://merchant.test:8443/webhooks", make_notification(), "message-1")

    assert route.calls.last.request.headers.get("Host") == "merchant.test:8443"


@respx.mock
async def test_send_pins_first_sorted_address(client: httpx.AsyncClient) -> None:
    route = respx.post("https://8.8.4.4/webhooks").mock(return_value=httpx.Response(200))
    sender = HttpxWebhookSender(
        client,
        trusted_hosts=frozenset(),
        resolver=resolver_returning(PUBLIC_ADDRESS, "8.8.4.4"),
    )

    await sender.send(WEBHOOK_URL, make_notification(), "message-1")

    assert route.call_count == 1


@respx.mock
async def test_send_wraps_ipv6_address_in_brackets(client: httpx.AsyncClient) -> None:
    route = respx.post("https://[2606:4700:4700::1111]/webhooks").mock(return_value=httpx.Response(200))
    sender = HttpxWebhookSender(
        client,
        trusted_hosts=frozenset(),
        resolver=resolver_returning("2606:4700:4700::1111"),
    )

    await sender.send(WEBHOOK_URL, make_notification(), "message-1")

    assert route.call_count == 1


@pytest.mark.parametrize("status_code", [301, 400, 500, 503])
@respx.mock
async def test_send_raises_on_non_success_status(sender: HttpxWebhookSender, status_code: int) -> None:
    respx.post(PINNED_URL).mock(return_value=httpx.Response(status_code))

    with pytest.raises(WebhookDeliveryError) as error_info:
        await sender.send(WEBHOOK_URL, make_notification(), "message-1")

    assert error_info.value.status_code == status_code


@pytest.mark.parametrize("error", [httpx.ConnectTimeout("t"), httpx.ConnectError("c")])
@respx.mock
async def test_send_raises_on_transport_error(sender: HttpxWebhookSender, error: httpx.HTTPError) -> None:
    respx.post(PINNED_URL).mock(side_effect=error)

    with pytest.raises(WebhookDeliveryError) as error_info:
        await sender.send(WEBHOOK_URL, make_notification(), "message-1")

    assert error_info.value.status_code is None


@pytest.mark.parametrize(
    "addresses",
    [
        ("127.0.0.1",),
        ("10.0.0.5",),
        ("192.168.1.1",),
        ("172.16.0.1",),
        ("169.254.169.254",),
        (UNSPECIFIED_ADDRESS,),
        ("::1",),
        ("fd00::1",),
        ("fe80::1%eth0",),
        ("224.0.0.1",),
        (PUBLIC_ADDRESS, "10.0.0.5"),
        (),
    ],
)
@respx.mock
async def test_send_refuses_non_public_targets(client: httpx.AsyncClient, addresses: tuple[str, ...]) -> None:
    route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
    sender = HttpxWebhookSender(client, trusted_hosts=frozenset(), resolver=resolver_returning(*addresses))

    with pytest.raises(WebhookDeliveryError) as error_info:
        await sender.send(WEBHOOK_URL, make_notification(), "message-1")

    assert {"status_code": error_info.value.status_code, "calls": route.call_count} == {
        "status_code": None,
        "calls": 0,
    }


@respx.mock
async def test_send_refuses_when_host_does_not_resolve(client: httpx.AsyncClient) -> None:
    route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
    sender = HttpxWebhookSender(client, trusted_hosts=frozenset(), resolver=failing_resolver)

    with pytest.raises(WebhookDeliveryError) as error_info:
        await sender.send(WEBHOOK_URL, make_notification(), "message-1")

    assert {"status_code": error_info.value.status_code, "calls": route.call_count} == {
        "status_code": None,
        "calls": 0,
    }


@respx.mock
async def test_send_posts_to_trusted_host_without_address_check(client: httpx.AsyncClient) -> None:
    route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
    sender = HttpxWebhookSender(
        client,
        trusted_hosts=frozenset({"merchant.test"}),
        resolver=resolver_returning("10.0.0.5"),
    )

    await sender.send(WEBHOOK_URL, make_notification(), "message-1")

    assert route.call_count == 1


@respx.mock
async def test_send_matches_trusted_host_case_insensitively(client: httpx.AsyncClient) -> None:
    route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
    sender = HttpxWebhookSender(
        client,
        trusted_hosts=frozenset({"merchant.test"}),
        resolver=resolver_returning("10.0.0.5"),
    )

    await sender.send("https://Merchant.TEST/webhooks", make_notification(), "message-1")

    assert route.call_count == 1


@respx.mock
async def test_send_refuses_private_target_outside_trusted_hosts(client: httpx.AsyncClient) -> None:
    route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
    sender = HttpxWebhookSender(
        client,
        trusted_hosts=frozenset({"webhook-mock"}),
        resolver=resolver_returning("10.0.0.5"),
    )

    with pytest.raises(WebhookDeliveryError):
        await sender.send(WEBHOOK_URL, make_notification(), "message-1")

    assert route.call_count == 0


async def test_resolve_host_returns_addresses_for_ip_literal() -> None:
    assert await resolve_host("127.0.0.1") == ["127.0.0.1"]
