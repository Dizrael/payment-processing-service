import pytest

from src.services.errors import WebhookDeliveryError


@pytest.mark.parametrize(
    ("url", "status_code", "expected"),
    [
        (
            "https://user:secret@hooks.example.com:8443/path?token=abc#frag",
            500,
            "Webhook delivery to hooks.example.com:8443 failed: status 500",
        ),
        (
            "http://hooks.example.com/callback?api_key=xyz",
            None,
            "Webhook delivery to hooks.example.com failed: transport error",
        ),
    ],
)
def test_webhook_delivery_error_message_omits_credentials_path_and_query(
    url: str, status_code: int | None, expected: str
) -> None:
    assert str(WebhookDeliveryError(url, status_code)) == expected
