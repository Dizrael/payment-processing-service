import asyncio
import ipaddress
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

import httpx

from src.db.models import Currency, PaymentStatus
from src.services.errors import WebhookDeliveryError


@dataclass(frozen=True, slots=True)
class WebhookNotification:
    payment_id: uuid.UUID
    status: PaymentStatus
    amount: Decimal
    currency: Currency
    failure_reason: str | None
    processed_at: datetime | None

    def to_json(self) -> dict[str, Any]:
        return {
            "payment_id": str(self.payment_id),
            "status": self.status.value,
            "amount": str(self.amount),
            "currency": self.currency.value,
            "failure_reason": self.failure_reason,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
        }


class WebhookSender(Protocol):
    async def send(self, url: str, notification: WebhookNotification, message_id: str) -> None: ...


Resolver = Callable[[str], Awaitable[list[str]]]


async def resolve_host(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    return sorted({str(info[4][0]) for info in infos})


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    return ip.is_global and not ip.is_multicast


class HttpxWebhookSender:
    def __init__(
        self,
        client: httpx.AsyncClient,
        trusted_hosts: frozenset[str],
        resolver: Resolver = resolve_host,
    ) -> None:
        self._client = client
        self._trusted_hosts = frozenset(host.lower() for host in trusted_hosts)
        self._resolver = resolver

    async def send(self, url: str, notification: WebhookNotification, message_id: str) -> None:
        target = httpx.URL(url)
        headers = {"X-Message-Id": message_id}
        extensions: dict[str, str] = {}
        if target.host not in self._trusted_hosts:
            address = await self._resolve_public_address(url, target.host)
            headers["Host"] = target.netloc.decode()
            extensions["sni_hostname"] = target.host
            target = target.copy_with(host=address)
        try:
            response = await self._client.post(
                target,
                json=notification.to_json(),
                headers=headers,
                extensions=extensions,
                follow_redirects=False,
            )
        except httpx.HTTPError as error:
            raise WebhookDeliveryError(url) from error
        if not response.is_success:
            raise WebhookDeliveryError(url, response.status_code)

    async def _resolve_public_address(self, url: str, host: str) -> str:
        try:
            addresses = await self._resolver(host)
        except OSError as error:
            raise WebhookDeliveryError(url) from error
        if not addresses or not all(is_public_address(address) for address in addresses):
            raise WebhookDeliveryError(url)
        return sorted(addresses)[0]
