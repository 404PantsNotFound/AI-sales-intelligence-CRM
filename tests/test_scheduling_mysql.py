"""Scheduling concurrency against real MySQL row locks.

Opt-in: set MYSQL_TEST_URL to a dedicated, disposable database, e.g.
mysql+pymysql://user:pass@localhost:3306/ai_sales_crm_test
Every table in that database is dropped and recreated per test.
"""

import os
import time
from collections.abc import Callable, Generator
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Engine, bindparam, create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.exceptions import APIError
from app.database.connection import Base
from app.models import Call, Company, Customer, Meeting, SchedulingLock
from app.schemas.call import CallCreate
from app.schemas.meeting import MeetingCreate
from app.services import call_service, meeting_service
from app.services.scheduling_service import _workspace_schedule_lock_statement

MYSQL_TEST_URL = os.getenv("MYSQL_TEST_URL")
pytestmark = pytest.mark.skipif(
    not MYSQL_TEST_URL, reason="MYSQL_TEST_URL is not set; MySQL locking tests skipped"
)

SLOT = datetime(2030, 1, 7, 10, 0, tzinfo=timezone.utc)
WAIT_SECONDS = 15
APPROVED_TEST_DATABASE = "ai_sales_crm_test"
# Any other query key (db, database, init_command, read_default_file, ...) could
# redirect the connection, so only harmless keys are allowed.
ALLOWED_URL_QUERY_KEYS = {"charset"}


@pytest.fixture
def mysql_engine() -> Generator[Engine, None, None]:
    url = make_url(MYSQL_TEST_URL or "")
    # Tables are dropped below, so never point this at a real database.
    if not url.drivername.startswith("mysql"):
        pytest.fail("MYSQL_TEST_URL must use a mysql driver.")
    database = (url.database or "").lower()
    if (
        not database.endswith("_test")
        or database == settings.db_name.lower()
        or database == "ai_sales_crm"
        or database != APPROVED_TEST_DATABASE
    ):
        pytest.fail(f"MYSQL_TEST_URL must name the {APPROVED_TEST_DATABASE} database.")
    if set(url.query) - ALLOWED_URL_QUERY_KEYS:
        pytest.fail("MYSQL_TEST_URL may only use these query options: charset.")
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as conn:
        connected = (conn.scalar(text("SELECT DATABASE()")) or "").lower()
    if connected != database:
        engine.dispose()
        pytest.fail("Connected database does not match the approved test database.")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(SchedulingLock(lock_id=1))
        db.commit()
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def mysql_sessions(mysql_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=mysql_engine, expire_on_commit=False)


@pytest.fixture
def customer_id(mysql_sessions: sessionmaker[Session]) -> int:
    with mysql_sessions() as db:
        company = Company(company_name="MySQL Lock Company")
        db.add(company)
        db.flush()
        customer = Customer(
            company_id=company.company_id,
            customer_name="MySQL Lock Customer",
            status="active",
            sales_stage="new",
        )
        db.add(customer)
        db.commit()
        return customer.customer_id


def _attempt(
    sessions: sessionmaker[Session],
    create: Callable[[Session], object],
    connection_ids: list[int],
) -> str:
    with sessions() as db:
        # Pins this session's connection so the gate can watch exactly this worker.
        connection_ids.append(db.scalar(text("SELECT CONNECTION_ID()")))
        try:
            create(db)
            return "committed"
        except APIError as exc:
            return exc.code


def _waiting_lock_requests(gate: Session, connection_ids: list[int]) -> int:
    if not connection_ids:
        return 0
    # Own-user threads are visible without the PROCESS privilege.
    return gate.scalar(
        text(
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE id IN :ids AND db = DATABASE() "
            "AND info LIKE 'SELECT%scheduling_locks%FOR UPDATE%'"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": list(connection_ids)},
    )


def _race(
    sessions: sessionmaker[Session],
    first: Callable[[Session], object],
    second: Callable[[Session], object],
) -> list[str]:
    """Queue both writers behind a held workspace lock, then release them together."""
    # Gate is entered last so it exits (releasing the lock) before the pool waits.
    with ThreadPoolExecutor(max_workers=2) as pool, sessions() as gate:
        assert gate.scalar(_workspace_schedule_lock_statement()) is not None
        connection_ids: list[int] = []
        futures: list[Future[str]] = [
            pool.submit(_attempt, sessions, first, connection_ids),
            pool.submit(_attempt, sessions, second, connection_ids),
        ]
        deadline = time.monotonic() + WAIT_SECONDS
        while _waiting_lock_requests(gate, connection_ids) < 2:
            if time.monotonic() > deadline or any(f.done() for f in futures):
                gate.rollback()
                pytest.fail(
                    "Both writers did not block on the scheduling lock: "
                    f"{[f.result() for f in futures if f.done()]}"
                )
            time.sleep(0.05)
        gate.rollback()
        return sorted(f.result(timeout=WAIT_SECONDS) for f in futures)


def _meeting(customer_id: int, start: datetime, minutes: int) -> Callable[[Session], object]:
    data = MeetingCreate(
        customer_id=customer_id, scheduled_at=start, duration=minutes, agenda="Race"
    )
    return lambda db: meeting_service.create_meeting(db, data)


def _call(customer_id: int, start: datetime, minutes: int) -> Callable[[Session], object]:
    data = CallCreate(customer_id=customer_id, scheduled_at=start, duration=minutes)
    return lambda db: call_service.create_call(db, data)


def _active_count(sessions: sessionmaker[Session]) -> int:
    with sessions() as db:
        meetings = db.scalar(
            select(func.count()).select_from(Meeting).where(Meeting.status == "scheduled")
        )
        calls = db.scalar(
            select(func.count()).select_from(Call).where(Call.status == "scheduled")
        )
        return meetings + calls


def test_concurrent_overlapping_meeting_and_call_commit_only_one(
    mysql_sessions: sessionmaker[Session], customer_id: int
) -> None:
    outcomes = _race(
        mysql_sessions,
        _meeting(customer_id, SLOT, 60),
        _call(customer_id, SLOT + timedelta(minutes=30), 30),
    )

    assert outcomes == ["committed", "schedule_conflict"]
    assert _active_count(mysql_sessions) == 1


def test_concurrent_back_to_back_meeting_and_call_both_commit(
    mysql_sessions: sessionmaker[Session], customer_id: int
) -> None:
    outcomes = _race(
        mysql_sessions,
        _meeting(customer_id, SLOT, 60),
        _call(customer_id, SLOT + timedelta(minutes=60), 30),
    )

    assert outcomes == ["committed", "committed"]
    assert _active_count(mysql_sessions) == 2
