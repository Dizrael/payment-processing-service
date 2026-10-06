import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from src.controllers.dependencies import PaymentServiceDep, verify_api_key
from src.schemas import PaymentAcceptedResponse, PaymentCreateRequest, PaymentResponse
from src.services.errors import IdempotencyConflictError, PaymentNotFoundError

router = APIRouter(
    prefix="/api/v1/payments",
    tags=["payments"],
    dependencies=[Depends(verify_api_key)],
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Invalid or missing API key"}},
)


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_202_ACCEPTED: {
            "headers": {"Location": {"description": "URL of the payment status", "schema": {"type": "string"}}}
        },
        status.HTTP_409_CONFLICT: {"description": "Idempotency-Key reused with a different body"},
    },
)
async def create_payment(
    body: PaymentCreateRequest,
    idempotency_key: Annotated[uuid.UUID, Header(alias="Idempotency-Key")],
    service: PaymentServiceDep,
    request: Request,
    response: Response,
) -> PaymentAcceptedResponse:
    try:
        payment = await service.create(body, idempotency_key)
    except IdempotencyConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    response.headers["Location"] = request.app.url_path_for("get_payment", payment_id=str(payment.id))
    return PaymentAcceptedResponse(id=payment.id, status=payment.status, created_at=payment.created_at)


@router.get(
    "/{payment_id}",
    responses={status.HTTP_404_NOT_FOUND: {"description": "Payment not found"}},
)
async def get_payment(payment_id: uuid.UUID, service: PaymentServiceDep) -> PaymentResponse:
    try:
        payment = await service.get(payment_id)
    except PaymentNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found") from error
    return PaymentResponse.model_validate(payment)
