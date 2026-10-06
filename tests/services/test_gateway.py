import random
import uuid
from dataclasses import asdict
from decimal import Decimal

import pytest

from src.db.models import Currency
from src.services.gateway import EmulatedGateway


class FixedRandom(random.Random):
    def __init__(self, roll: float) -> None:
        super().__init__()
        self.roll = roll
        self.uniform_bounds: list[tuple[float, float]] = []

    def random(self) -> float:
        return self.roll

    def uniform(self, a: float, b: float) -> float:
        self.uniform_bounds.append((a, b))
        return (a + b) / 2


@pytest.fixture
def recorded_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("src.services.gateway.asyncio.sleep", fake_sleep)
    return sleeps


async def charge(gateway: EmulatedGateway) -> dict[str, object]:
    result = await gateway.charge(uuid.uuid4(), Decimal("10.00"), Currency.EUR)
    return asdict(result)


async def test_charge_succeeds_when_roll_is_below_success_rate(
    recorded_sleeps: list[float],
) -> None:
    gateway = EmulatedGateway(2, 5, 0.9, rng=FixedRandom(0.89))

    assert {"result": await charge(gateway), "sleeps": recorded_sleeps} == {
        "result": {"succeeded": True, "failure_reason": None},
        "sleeps": [3.5],
    }


@pytest.mark.parametrize("roll", [0.9, 0.99])
@pytest.mark.usefixtures("recorded_sleeps")
async def test_charge_declines_when_roll_reaches_success_rate(roll: float) -> None:
    gateway = EmulatedGateway(2, 5, 0.9, rng=FixedRandom(roll))

    assert await charge(gateway) == {
        "succeeded": False,
        "failure_reason": "Declined by gateway",
    }


async def test_charge_delay_uses_configured_bounds(recorded_sleeps: list[float]) -> None:
    rng = FixedRandom(0.0)
    gateway = EmulatedGateway(2, 5, 0.9, rng=rng)

    await charge(gateway)

    assert {"bounds": rng.uniform_bounds, "sleeps": recorded_sleeps} == {
        "bounds": [(2, 5)],
        "sleeps": [3.5],
    }
