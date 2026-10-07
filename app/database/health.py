import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database.connection import engine

logger = logging.getLogger(__name__)


def check_database_connection() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("Database connectivity check failed.")
        return False
    return True
