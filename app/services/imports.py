from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.services.import_service import import_crm_workbook

router = APIRouter(prefix="/import", tags=["import"])


@router.post("", status_code=status.HTTP_200_OK)
def import_workbook(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        return {
            "message": "Only .xlsx Excel files are supported."
        }

    file_bytes = file.file.read()

    result = import_crm_workbook(db, file_bytes)

    return {
        "message": "CRM workbook imported successfully.",
        "filename": file.filename,
        "counts": result,
    }

