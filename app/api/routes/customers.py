from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.company import CompanyResponse
from app.schemas.contact import ContactResponse
from app.schemas.customer import (
    CustomerDetailResponse,
    CustomerListItem,
    CustomerListResponse,
    CustomerResponse,
    CustomerUpdate,
)
from app.schemas.customer_registration import (
    CustomerRegistrationCreate,
    CustomerRegistrationResponse,
)
from app.schemas.sales_enquiry import SalesEnquiryResponse
from app.services import customer_service

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("/test")
def test_customers() -> dict[str, str]:
    return {"status": "ok", "module": "customers"}


@router.post(
    "",
    response_model=CustomerRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_customer(
    registration: CustomerRegistrationCreate,
    db: Session = Depends(get_db),
) -> CustomerRegistrationResponse:
    customer, company, contact, sales_enquiry = customer_service.create_customer(
        db,
        registration,
    )
    return CustomerRegistrationResponse(
        message="Customer registered successfully",
        customer=CustomerResponse.model_validate(customer),
        company=CompanyResponse.model_validate(company),
        contact=ContactResponse.model_validate(contact),
        sales_enquiry=SalesEnquiryResponse.model_validate(sales_enquiry),
    )


@router.get("", response_model=CustomerListResponse)
def get_customers(
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=255),
    customer_name: str | None = Query(default=None, max_length=255),
    company_name: str | None = Query(default=None, max_length=255),
) -> CustomerListResponse:
    customers, total = customer_service.list_customers(
        db,
        page=page,
        page_size=page_size,
        search=search,
        customer_name=customer_name,
        company_name=company_name,
    )
    return CustomerListResponse(
        items=[CustomerListItem.model_validate(customer) for customer in customers],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/{customer_id}", response_model=CustomerDetailResponse)
def get_customer(
    customer_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> CustomerDetailResponse:
    customer = customer_service.get_customer(db, customer_id)
    return CustomerDetailResponse.model_validate(customer)


@router.put(
    "/{customer_id}",
    response_model=CustomerResponse,
    summary="Update a customer record",
)
@router.patch(
    "/{customer_id}",
    response_model=CustomerResponse,
    summary="Partially update a customer record",
)
def update_customer(
    data: CustomerUpdate,
    customer_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> CustomerResponse:
    customer = customer_service.update_customer(db, customer_id, data)
    return CustomerResponse.model_validate(customer)

