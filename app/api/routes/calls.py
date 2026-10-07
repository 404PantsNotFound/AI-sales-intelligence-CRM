from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.call import CallCreate, CallResponse, CallUpdate
from app.services import call_service

router = APIRouter(prefix="/calls", tags=["calls"])
customer_router = APIRouter(prefix="/customers", tags=["customer calls"])


@router.get("/test")
def test_calls() -> dict[str, str]:
    return {"status": "ok", "module": "calls"}


@router.post(
    "",
    response_model=CallResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Log a customer call",
)
def create_call(data: CallCreate, db: Session = Depends(get_db)) -> CallResponse:
    return CallResponse.model_validate(call_service.create_call(db, data))


@router.get("/{call_id}", response_model=CallResponse, summary="Get a call")
def get_call(call_id: int = Path(ge=1), db: Session = Depends(get_db)) -> CallResponse:
    return CallResponse.model_validate(call_service.get_call(db, call_id))


@router.put("/{call_id}", response_model=CallResponse, summary="Update a call")
def update_call(
    data: CallUpdate,
    call_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> CallResponse:
    return CallResponse.model_validate(call_service.update_call(db, call_id, data))


@customer_router.get(
    "/{customer_id}/calls",
    response_model=list[CallResponse],
    summary="List a customer's calls",
)
def list_customer_calls(
    customer_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> list[CallResponse]:
    return [
        CallResponse.model_validate(item)
        for item in call_service.list_customer_calls(db, customer_id)
    ]

