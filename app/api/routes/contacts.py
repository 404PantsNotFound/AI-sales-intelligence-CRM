from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.contact import ContactCreate, ContactResponse, ContactUpdate
from app.services import contact_service

router = APIRouter(prefix="/contacts", tags=["contacts"])
customer_router = APIRouter(prefix="/customers", tags=["customer contacts"])


@router.get("/test")
def test_contacts() -> dict[str, str]:
    return {"status": "ok", "module": "contacts"}


@router.post(
    "",
    response_model=ContactResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a customer contact",
)
def create_contact(
    data: ContactCreate,
    db: Session = Depends(get_db),
) -> ContactResponse:
    return ContactResponse.model_validate(contact_service.create_contact(db, data))


@router.get(
    "/{contact_id}",
    response_model=ContactResponse,
    summary="Get a customer contact",
)
def get_contact(
    contact_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> ContactResponse:
    return ContactResponse.model_validate(contact_service.get_contact(db, contact_id))


@router.put(
    "/{contact_id}",
    response_model=ContactResponse,
    summary="Update a customer contact",
)
@router.patch(
    "/{contact_id}",
    response_model=ContactResponse,
    summary="Partially update a customer contact",
)
def update_contact(
    data: ContactUpdate,
    contact_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> ContactResponse:
    return ContactResponse.model_validate(
        contact_service.update_contact(db, contact_id, data)
    )


@customer_router.get(
    "/{customer_id}/contacts",
    response_model=list[ContactResponse],
    summary="List a customer's contacts",
)
def list_customer_contacts(
    customer_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> list[ContactResponse]:
    return [
        ContactResponse.model_validate(item)
        for item in contact_service.list_customer_contacts(db, customer_id)
    ]

