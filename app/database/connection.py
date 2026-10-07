from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""


engine: Engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,
)
SessionLocal = sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def rollback_failed_transaction(db: Session) -> None:
    """Clear a failed transaction before the request-scoped Session is reused."""
    db.rollback()


@contextmanager
def atomic_transaction(db: Session) -> Generator[None, None, None]:
    if not db.in_transaction():
        with db.begin():
            yield
    else:
        try:
            yield
            db.commit()
        except Exception:
            db.rollback()
            raise
