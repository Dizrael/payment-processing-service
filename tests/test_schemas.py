import pytest
from pydantic import ValidationError

from src.db.models import Payment
from src.schemas import PaymentCreateRequest, PaymentResponse

VALID_BODY: dict[str, object] = {
    "amount": "100.5",
    "currency": "RUB",
    "description": "Order #42",
    "metadata": {"order_id": 42},
    "webhook_url": "https://merchant.test/webhooks",
}


def test_amount_is_normalized_to_two_decimal_places() -> None:
    request = PaymentCreateRequest.model_validate(VALID_BODY)

    assert request.model_dump(mode="json", by_alias=True) == {**VALID_BODY, "amount": "100.50"}


def test_metadata_defaults_to_empty_dict() -> None:
    body = {key: value for key, value in VALID_BODY.items() if key != "metadata"}

    assert PaymentCreateRequest.model_validate(body).model_dump(mode="json", by_alias=True) == {
        **VALID_BODY,
        "amount": "100.50",
        "metadata": {},
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount", "0"),
        ("amount", "-1"),
        ("amount", "1.001"),
        ("currency", "GBP"),
        ("description", ""),
        ("description", "x" * 501),
        ("webhook_url", "not-a-url"),
        ("webhook_url", "ftp://merchant.test/hook"),
        ("metadata", {"blob": "x" * 4096}),
    ],
)
def test_invalid_fields_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        PaymentCreateRequest.model_validate({**VALID_BODY, field: value})


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PaymentCreateRequest.model_validate({**VALID_BODY, "status": "succeeded"})


def test_metadata_is_exposed_as_meta_attribute() -> None:
    request = PaymentCreateRequest.model_validate(VALID_BODY)

    assert {"attribute": request.meta, "dump": request.model_dump()["meta"]} == {
        "attribute": {"order_id": 42},
        "dump": {"order_id": 42},
    }


def test_meta_field_name_is_not_accepted_from_clients() -> None:
    body = {key: value for key, value in VALID_BODY.items() if key != "metadata"}

    with pytest.raises(ValidationError):
        PaymentCreateRequest.model_validate({**body, "meta": {"order_id": 42}})


def test_every_response_field_is_payment_column() -> None:
    columns = {attribute.key for attribute in Payment.__mapper__.column_attrs}

    assert set(PaymentResponse.model_fields) - columns == set()
