from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.database.connection import get_db
from app.models import User
from app.schemas.sales_enquiry import (
    SalesEnquiryCreate,
    SalesEnquiryResponse,
    SalesEnquiryUpdate,
)
from app.services import enquiry_service

router = APIRouter(prefix="/enquiries", tags=["enquiries"])
customer_router = APIRouter(prefix="/customers", tags=["customer enquiries"])


@router.get("/test")
def test_enquiries() -> dict[str, str]:
    return {"status": "ok", "module": "enquiries"}


@router.post(
    "",
    response_model=SalesEnquiryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a sales enquiry",
)
def create_enquiry(
    data: SalesEnquiryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SalesEnquiryResponse:
    return SalesEnquiryResponse.model_validate(
        enquiry_service.create_enquiry(db, data, organization_id=current_user.organization_id)
    )


@router.get(
    "/{enquiry_id}",
    response_model=SalesEnquiryResponse,
    summary="Get a sales enquiry",
)
def get_enquiry(
    enquiry_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SalesEnquiryResponse:
    return SalesEnquiryResponse.model_validate(
        enquiry_service.get_enquiry(db, enquiry_id, organization_id=current_user.organization_id)
    )


@router.put(
    "/{enquiry_id}",
    response_model=SalesEnquiryResponse,
    summary="Update a sales enquiry",
)
@router.patch(
    "/{enquiry_id}",
    response_model=SalesEnquiryResponse,
    summary="Partially update a sales enquiry",
)
def update_enquiry(
    data: SalesEnquiryUpdate,
    enquiry_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SalesEnquiryResponse:
    return SalesEnquiryResponse.model_validate(
        enquiry_service.update_enquiry(
            db, enquiry_id, data, organization_id=current_user.organization_id
        )
    )


@customer_router.get(
    "/{customer_id}/enquiries",
    response_model=list[SalesEnquiryResponse],
    summary="List a customer's sales enquiries",
)
def list_customer_enquiries(
    customer_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SalesEnquiryResponse]:
    return [
        SalesEnquiryResponse.model_validate(item)
        for item in enquiry_service.list_customer_enquiries(
            db, customer_id, organization_id=current_user.organization_id
        )
    ]

