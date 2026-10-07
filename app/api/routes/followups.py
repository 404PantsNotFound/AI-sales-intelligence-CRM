from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.follow_up import FollowUpCreate, FollowUpResponse, FollowUpUpdate
from app.services import followup_service

router = APIRouter(prefix="/followups", tags=["followups"])
customer_router = APIRouter(prefix="/customers", tags=["customer follow-ups"])


@router.get("/test")
def test_followups() -> dict[str, str]:
    return {"status": "ok", "module": "followups"}


@router.post(
    "",
    response_model=FollowUpResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a customer follow-up",
)
def create_followup(
    data: FollowUpCreate,
    db: Session = Depends(get_db),
) -> FollowUpResponse:
    return FollowUpResponse.model_validate(followup_service.create_followup(db, data))


@router.get("/{followup_id}", response_model=FollowUpResponse, summary="Get a follow-up")
def get_followup(
    followup_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> FollowUpResponse:
    return FollowUpResponse.model_validate(followup_service.get_followup(db, followup_id))


@router.put(
    "/{followup_id}",
    response_model=FollowUpResponse,
    summary="Update a follow-up",
)
def update_followup(
    data: FollowUpUpdate,
    followup_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> FollowUpResponse:
    return FollowUpResponse.model_validate(
        followup_service.update_followup(db, followup_id, data)
    )


@customer_router.get(
    "/{customer_id}/followups",
    response_model=list[FollowUpResponse],
    summary="List a customer's follow-ups",
)
def list_customer_followups(
    customer_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> list[FollowUpResponse]:
    return [
        FollowUpResponse.model_validate(item)
        for item in followup_service.list_customer_followups(db, customer_id)
    ]

