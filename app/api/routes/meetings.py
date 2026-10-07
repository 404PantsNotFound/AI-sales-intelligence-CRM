from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.meeting import MeetingCreate, MeetingResponse, MeetingUpdate
from app.services import meeting_service

router = APIRouter(prefix="/meetings", tags=["meetings"])
customer_router = APIRouter(prefix="/customers", tags=["customer meetings"])


@router.get("/test")
def test_meetings() -> dict[str, str]:
    return {"status": "ok", "module": "meetings"}


@router.post(
    "",
    response_model=MeetingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a customer meeting",
)
def create_meeting(data: MeetingCreate, db: Session = Depends(get_db)) -> MeetingResponse:
    return MeetingResponse.model_validate(meeting_service.create_meeting(db, data))


@router.get("/{meeting_id}", response_model=MeetingResponse, summary="Get a meeting")
def get_meeting(
    meeting_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> MeetingResponse:
    return MeetingResponse.model_validate(meeting_service.get_meeting(db, meeting_id))


@router.put("/{meeting_id}", response_model=MeetingResponse, summary="Update a meeting")
def update_meeting(
    data: MeetingUpdate,
    meeting_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> MeetingResponse:
    return MeetingResponse.model_validate(
        meeting_service.update_meeting(db, meeting_id, data)
    )


@customer_router.get(
    "/{customer_id}/meetings",
    response_model=list[MeetingResponse],
    summary="List a customer's meetings",
)
def list_customer_meetings(
    customer_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> list[MeetingResponse]:
    return [
        MeetingResponse.model_validate(item)
        for item in meeting_service.list_customer_meetings(db, customer_id)
    ]

