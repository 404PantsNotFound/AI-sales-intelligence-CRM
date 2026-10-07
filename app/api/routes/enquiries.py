from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
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
) -> SalesEnquiryResponse:
    return SalesEnquiryResponse.model_validate(enquiry_service.create_enquiry(db, data))


@router.get(
    "/{enquiry_id}",
    response_model=SalesEnquiryResponse,
    summary="Get a sales enquiry",
)
def get_enquiry(
    enquiry_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> SalesEnquiryResponse:
    return SalesEnquiryResponse.model_validate(
        enquiry_service.get_enquiry(db, enquiry_id)
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
) -> SalesEnquiryResponse:
    return SalesEnquiryResponse.model_validate(
        enquiry_service.update_enquiry(db, enquiry_id, data)
    )


@customer_router.get(
    "/{customer_id}/enquiries",
    response_model=list[SalesEnquiryResponse],
    summary="List a customer's sales enquiries",
)
def list_customer_enquiries(
    customer_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> list[SalesEnquiryResponse]:
    return [
        SalesEnquiryResponse.model_validate(item)
        for item in enquiry_service.list_customer_enquiries(db, customer_id)
    ]

