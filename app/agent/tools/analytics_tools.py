import json
from datetime import date

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from app.services import analytics_service


class SalesAnalyticsInput(BaseModel):
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def date_range_is_valid(self) -> "SalesAnalyticsInput":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        return self


def build_analytics_tools(db: Session) -> list[StructuredTool]:
    def get_sales_analytics(
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> str:
        """Retrieve deterministic, pre-calculated CRM overview, pipeline, enquiry, activity and follow-up analytics."""
        results = {
            "overview": analytics_service.get_overview_metrics(
                db, start_date, end_date
            ).model_dump(mode="json"),
            "customers": analytics_service.get_customer_metrics(
                db, start_date, end_date
            ).model_dump(mode="json"),
            "enquiries": analytics_service.get_enquiry_metrics(
                db, start_date, end_date
            ).model_dump(mode="json"),
            "activities": analytics_service.get_activity_metrics(
                db, start_date, end_date
            ).model_dump(mode="json"),
            "pipeline": analytics_service.get_pipeline_metrics(
                db, start_date, end_date
            ).model_dump(mode="json"),
            "followups": analytics_service.get_followup_metrics(
                db, start_date, end_date
            ).model_dump(mode="json"),
        }
        return json.dumps(results, ensure_ascii=False)

    return [
        StructuredTool.from_function(
            func=get_sales_analytics,
            name="get_sales_analytics",
            description=get_sales_analytics.__doc__,
            args_schema=SalesAnalyticsInput,
        )
    ]
