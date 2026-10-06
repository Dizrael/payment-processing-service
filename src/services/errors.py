import uuid

import httpx


class PaymentNotFoundError(Exception):
    def __init__(self, payment_id: uuid.UUID) -> None:
        super().__init__(f"Payment {payment_id} not found")
        self.payment_id = payment_id


class IdempotencyConflictError(Exception):
    def __init__(self) -> None:
        super().__init__("Idempotency-Key was already used with a different request body")


class PublishRejectedError(Exception):
    def __init__(self, message_id: uuid.UUID) -> None:
        super().__init__(f"Broker rejected message {message_id}")


class WebhookDeliveryError(Exception):
    def __init__(self, url: str, status_code: int | None = None) -> None:
        reason = "transport error" if status_code is None else f"status {status_code}"
        super().__init__(f"Webhook delivery to {httpx.URL(url).netloc.decode()} failed: {reason}")
        self.status_code = status_code
