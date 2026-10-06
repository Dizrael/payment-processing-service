import secrets
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final


class Sentinel:
    pass


class Random(Sentinel):
    def uuid4(self) -> uuid.UUID:
        return uuid.uuid4()

    def token(self, nbytes: int = 4) -> str:
        return secrets.token_hex(nbytes)

    def decimal(self, minimum: int = 1, maximum: int = 10_000_000, places: int = 2) -> Decimal:
        return Decimal(minimum + secrets.randbelow(maximum - minimum + 1)).scaleb(-places)

    def choice[T](self, options: Sequence[T]) -> T:
        return secrets.choice(options)

    def __repr__(self) -> str:
        return "RANDOM"


class DatetimeNow(Sentinel):
    def __init__(self, tolerance: timedelta = timedelta(seconds=1), offset: timedelta = timedelta()) -> None:
        self.tolerance = tolerance
        self.offset = offset

    def generate(self) -> datetime:
        return datetime.now(UTC) + self.offset

    def __add__(self, offset: timedelta) -> "DatetimeNow":
        return DatetimeNow(self.tolerance, self.offset + offset)

    def __sub__(self, offset: timedelta) -> "DatetimeNow":
        return self + -offset

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            other = datetime.fromisoformat(other)
        if not isinstance(other, datetime) or other.tzinfo is None:
            return False
        return abs(other - self.generate()) <= self.tolerance

    def __hash__(self) -> int:
        return hash((self.tolerance, self.offset))

    def __repr__(self) -> str:
        return f"DATETIME_NOW + {self.offset} ± {self.tolerance}"


RANDOM: Final = Random()
DATETIME_NOW: Final = DatetimeNow()


def coalesce[T](value: T | Sentinel, default: Callable[[], T]) -> T:
    return default() if isinstance(value, Sentinel) else value
