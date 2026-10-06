import asyncio
import logging
import random
import secrets
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from src.db.models import Currency

logger = logging.getLogger(__name__)

DECLINED_REASON = "Declined by gateway"


@dataclass(frozen=True, slots=True)
class GatewayResult:
    succeeded: bool
    failure_reason: str | None = None


class PaymentGateway(Protocol):
    async def charge(self, payment_id: uuid.UUID, amount: Decimal, currency: Currency) -> GatewayResult: ...


class EmulatedGateway:
    def __init__(
        self,
        min_delay_seconds: float,
        max_delay_seconds: float,
        success_rate: float,
        rng: random.Random | None = None,
    ) -> None:
        self._min_delay_seconds = min_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._success_rate = success_rate
        self._rng = rng or secrets.SystemRandom()

    async def charge(self, payment_id: uuid.UUID, amount: Decimal, currency: Currency) -> GatewayResult:
        logger.info(
            "Charging payment",
            extra={"payment_id": str(payment_id), "amount": str(amount), "currency": currency.value},
        )
        await asyncio.sleep(self._rng.uniform(self._min_delay_seconds, self._max_delay_seconds))
        if self._rng.random() < self._success_rate:
            return GatewayResult(succeeded=True)
        return GatewayResult(succeeded=False, failure_reason=DECLINED_REASON)
