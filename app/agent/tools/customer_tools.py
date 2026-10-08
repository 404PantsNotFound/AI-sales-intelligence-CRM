from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from langchain_core.tools import StructuredTool

from app.core.exceptions import APIError
from app.database.connection import SessionLocal
from app.schemas.customer import CustomerDetailResponse, CustomerListItem
from app.schemas.sales_enquiry import SalesEnquiryResponse
from app.services import customer_service

from .common import bounded_records, error_output, success_output
from .context import AgentToolContext
from .schemas import CustomerIdInput, FindCustomerInput


def build_customer_tools(
    db: Session | None = None,
    context: AgentToolContext | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> list[StructuredTool]:

    factory = session_factory

    def get_session() -> tuple[Session, bool]:
        if factory is not None:
            return factory(), True
        if db is not None:
            return db, False
        return SessionLocal(), True

    def database_error(exc: SQLAlchemyError) -> str:
        return (
            '{"error":{'
            '"code":"database_error",'
            '"message":"The CRM database operation failed. '
            'The transaction was rolled back; please retry the CRM read."'
            "}}"
        )

    def find_customer(search: str) -> str:
        """Search for CRM customers by customer name or company name."""
        session, owns_session = get_session()

        try:
            customers, total = customer_service.list_customers(
                session,
                page=1,
                page_size=10,
                search=search,
            )

            if context is not None and context.locked_customer_id is not None:
                customers = [
                    customer
                    for customer in customers
                    if customer.customer_id == context.locked_customer_id
                ]
                total = len(customers)

            if context is not None:
                for customer in customers:
                    context.remember_customer(customer.customer_id)

            records = [
                CustomerListItem.model_validate(customer).model_dump(mode="json")
                for customer in customers
            ]

            return success_output({"items": records, "total": total})

        except APIError as exc:
            return error_output(exc)

        except SQLAlchemyError as exc:
            session.rollback()
            return database_error(exc)

        finally:
            if owns_session:
                session.close()

    def get_customer_profile(customer_id: int) -> str:
        """Retrieve a customer's profile, company, contacts, and sales enquiries."""
        session, owns_session = get_session()

        try:
            if context is not None:
                context.ensure_customer_access(customer_id)

            customer = customer_service.get_customer(session, customer_id)
            detail = CustomerDetailResponse.model_validate(customer).model_dump(
                mode="json"
            )

            if context is not None:
                context.remember_profile(detail)

            contacts = bounded_records(detail["contacts"])
            enquiries = bounded_records(detail["sales_enquiries"])

            profile = {
                "customer": {
                    key: detail[key]
                    for key in (
                        "customer_id",
                        "customer_name",
                        "status",
                        "sales_stage",
                        "created_at",
                        "updated_at",
                    )
                },
                "company": detail["company"],
                "contacts": contacts["items"],
                "sales_enquiries": enquiries["items"],
                "truncated": {
                    "contacts": contacts["truncated"],
                    "sales_enquiries": enquiries["truncated"],
                },
            }

            return success_output(profile)

        except APIError as exc:
            return error_output(exc)

        except SQLAlchemyError as exc:
            session.rollback()
            return database_error(exc)

        finally:
            if owns_session:
                session.close()

    def get_sales_enquiries(customer_id: int) -> str:
        """List the customer's sales enquiries, including product, status, and value."""
        session, owns_session = get_session()

        try:
            if context is not None:
                context.ensure_customer_access(customer_id)

            customer = customer_service.get_customer(session, customer_id)

            if context is not None:
                context.remember_customer(customer.customer_id)

            records = [
                SalesEnquiryResponse.model_validate(enquiry).model_dump(mode="json")
                for enquiry in customer.sales_enquiries
            ]

            if context is not None:
                for record in records:
                    context.remember_enquiry(
                        customer.customer_id,
                        record["enquiry_id"],
                    )

            return success_output(bounded_records(records))

        except APIError as exc:
            return error_output(exc)

        except SQLAlchemyError as exc:
            session.rollback()
            return database_error(exc)

        finally:
            if owns_session:
                session.close()

    return [
        StructuredTool.from_function(
            func=find_customer,
            name="find_customer",
            description=find_customer.__doc__,
            args_schema=FindCustomerInput,
        ),
        StructuredTool.from_function(
            func=get_customer_profile,
            name="get_customer_profile",
            description=get_customer_profile.__doc__,
            args_schema=CustomerIdInput,
        ),
        StructuredTool.from_function(
            func=get_sales_enquiries,
            name="get_sales_enquiries",
            description=get_sales_enquiries.__doc__,
            args_schema=CustomerIdInput,
        ),
    ]
