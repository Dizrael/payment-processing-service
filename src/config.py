from functools import lru_cache
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.logger import LogLevel


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key: SecretStr = Field(min_length=1)
    database_url: str
    rabbitmq_url: str
    log_level: LogLevel = "INFO"

    processing_max_attempts: int = Field(default=3, ge=1)
    processing_retry_base_delay_seconds: float = Field(default=2, ge=0)
    consumer_prefetch_count: int = Field(default=10, ge=1)
    queue_delivery_limit: int = Field(default=3, ge=1)
    queue_max_length: int = Field(default=100_000, ge=1)

    outbox_poll_interval_seconds: float = Field(default=1, gt=0)
    outbox_batch_size: int = Field(default=100, ge=1)

    webhook_timeout_seconds: float = Field(default=5, gt=0)
    webhook_trusted_hosts: frozenset[str] = frozenset()

    gateway_min_delay_seconds: float = Field(default=2, ge=0)
    gateway_max_delay_seconds: float = Field(default=5, ge=0)
    gateway_success_rate: float = Field(default=0.9, ge=0, le=1)

    @model_validator(mode="after")
    def check_gateway_delays(self) -> Self:
        if self.gateway_min_delay_seconds > self.gateway_max_delay_seconds:
            msg = "gateway_min_delay_seconds must not exceed gateway_max_delay_seconds"
            raise ValueError(msg)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
