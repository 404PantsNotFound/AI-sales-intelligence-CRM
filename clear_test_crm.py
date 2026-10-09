from app.database.connection import SessionLocal
from sqlalchemy import text


TABLES = [
    "follow_ups",
    "calls",
    "meetings",
    "sales_enquiries",
    "contacts",
    "customers",
    "companies",
]


def main() -> None:
    db = SessionLocal()

    try:
        # Disable FK checks temporarily so the cleanup is deterministic.
        db.execute(text("SET FOREIGN_KEY_CHECKS = 0"))

        for table in TABLES:
            db.execute(text(f"DELETE FROM {table}"))
            db.execute(text(f"ALTER TABLE {table} AUTO_INCREMENT = 1"))

        db.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        db.commit()

        print("CRM test data cleared successfully.")
        print("Users were NOT deleted.")

    except Exception:
        db.rollback()
        db.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()