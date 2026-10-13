from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.orm import Session

from app.core.exceptions import APIError
from app.core.security import get_current_user
from app.database.connection import get_db
from app.models import User
from app.services.import_service import import_crm_workbook

router = APIRouter(prefix="/import", tags=["import"])


@router.post("", status_code=status.HTTP_200_OK)
def import_workbook(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise APIError(
            "Only .xlsx Excel files are supported.",
            status_code=400,
            code="invalid_file_type",
        )

    file_bytes = file.file.read()

    if not file_bytes:
        raise APIError(
            "The uploaded file is empty.",
            status_code=400,
            code="empty_file",
        )

    result = import_crm_workbook(db, file_bytes, organization_id=current_user.organization_id)

    return {
        "message": "CRM workbook imported successfully.",
        "filename": file.filename,
        "counts": result,
    }

