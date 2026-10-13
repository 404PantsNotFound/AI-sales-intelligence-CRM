"""Phase 1: organisation membership, and registration error handling against it."""

from collections.abc import Generator
import importlib.util
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import hash_password
from app.database.connection import Base, get_db
from app.main import app
from app.models import Organization, User
from app.models.organization import LEGACY_ORGANIZATION_NAME, LEGACY_ORGANIZATION_SLUG
from app.services import auth_service
from tests.conftest import legacy_organization_id

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = {
    "email": "new.person@example.com",
    "password": "StrongPassword!234",
    "full_name": "New Person",
}
# Words that would reveal database internals if they reached a client.
INTERNAL_MARKERS = (
    "users", "organization_id", "ix_", "fk_", "integrityerror", "constraint",
    "insert", "select", "sqlite", "mysql",
)


@pytest.fixture
def fk_sessions() -> Generator[sessionmaker[Session], None, None]:
    """A SQLite engine that enforces foreign keys, unlike the shared test engines."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    event.listen(
        engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON")
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _client_for(sessions: sessionmaker[Session]) -> TestClient:
    def override_get_db() -> Generator[Session, None, None]:
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


@pytest.fixture
def registration_client(
    agent_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(settings, "allow_public_registration", True)
    with _client_for(agent_sessions) as client:
        yield client
    app.dependency_overrides.pop(get_db, None)


def _seed(sessions: sessionmaker[Session], email: str) -> None:
    with sessions() as db:
        db.add(
            User(
                email=email,
                full_name="Existing User",
                password_hash=hash_password("ExistingPassword!1"),
                role="sales",
                is_active=True,
                organization_id=legacy_organization_id(db),
            )
        )
        db.commit()


def _user_count(sessions: sessionmaker[Session], email: str) -> int:
    with sessions() as db:
        return int(
            db.scalar(select(func.count()).select_from(User).where(User.email == email)) or 0
        )


def _email_exists_sequence(monkeypatch: pytest.MonkeyPatch, *results: Any) -> list[str]:
    """Replace _email_exists; each call takes the next result (an exception is raised)."""
    real = auth_service._email_exists
    calls: list[str] = []

    def fake(db: Session, email: str) -> bool:
        calls.append(email)
        result = results[len(calls) - 1] if len(calls) <= len(results) else "real"
        if result == "real":
            return real(db, email)
        if isinstance(result, Exception):
            raise result
        return bool(result)

    monkeypatch.setattr(auth_service, "_email_exists", fake)
    return calls


def _assert_no_internals(body: dict[str, Any]) -> None:
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    text = f"{body['error']['code']} {body['error']['message']}".lower()
    for marker in INTERNAL_MARKERS:
        assert marker not in text, marker


# --- membership ------------------------------------------------------------------


def test_existing_user_keeps_access_in_legacy_workspace(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
) -> None:
    me = agent_client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert agent_client.get("/api/customers").status_code == 200
    assert agent_client.get("/api/integrations/google-calendar").status_code == 200

    with agent_sessions() as db:
        user = db.scalar(select(User).where(User.user_id == me.json()["user_id"]))
        assert user is not None
        assert user.role == "sales"
        assert user.organization.name == LEGACY_ORGANIZATION_NAME
        assert user.organization.slug == LEGACY_ORGANIZATION_SLUG
        assert user.organization.status == "active"
        # Stored but not enforced in Phase 1: null fields still grant full access.
        assert user.email_verified_at is None
        assert user.verification_grace_until is None


def test_organization_constraints(fk_sessions: sessionmaker[Session]) -> None:
    with fk_sessions() as db:
        db.add(Organization(name="One", slug="one"))
        db.commit()

        db.add(Organization(name="Copy", slug="one"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add(Organization(name="Bad", slug="bad", status="archived"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add(
            User(
                email="orphan@example.com",
                full_name="Orphan",
                password_hash="test-only-hash",
                role="sales",
                is_active=True,
                organization_id=999999,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


# --- registration (spec §D) --------------------------------------------------------


def test_registration_disabled_by_flag_never_touches_users(
    registration_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "allow_public_registration", False)
    calls = _email_exists_sequence(monkeypatch)

    response = registration_client.post("/api/auth/register", json=REGISTRATION)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "registration_disabled"
    assert calls == []


def test_duplicate_email_rejected_by_precheck(
    registration_client: TestClient,
    agent_sessions: sessionmaker[Session],
) -> None:
    _seed(agent_sessions, "duplicate@example.com")

    response = registration_client.post(
        "/api/auth/register", json={**REGISTRATION, "email": " DUPLICATE@Example.com "}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_email"
    assert _user_count(agent_sessions, "duplicate@example.com") == 1


def test_duplicate_email_race_is_classified_by_recheck(
    registration_client: TestClient,
    agent_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A concurrent signup commits between the pre-check and the insert.
    _seed(agent_sessions, "race@example.com")
    with agent_sessions() as db:
        organization_id = legacy_organization_id(db)
    calls = _email_exists_sequence(monkeypatch, False)
    logged: list[BaseException] = []
    monkeypatch.setattr(
        auth_service,
        "log_database_exception",
        lambda _logger, _operation, exc, **_: logged.append(exc),
    )

    # Give the new user a valid membership so the flush reaches the unique email index.
    @event.listens_for(agent_sessions, "before_flush")
    def assign_membership(session: Session, *_: object) -> None:
        for obj in session.new:
            if isinstance(obj, User):
                obj.organization_id = organization_id

    try:
        response = registration_client.post(
            "/api/auth/register", json={**REGISTRATION, "email": "race@example.com"}
        )
    finally:
        event.remove(agent_sessions, "before_flush", assign_membership)

    assert len(logged) == 1 and isinstance(logged[0], IntegrityError)
    assert "UNIQUE constraint failed: users.email" in str(logged[0].orig)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_email"
    assert len(calls) == 2
    assert _user_count(agent_sessions, "race@example.com") == 1


def test_missing_organization_membership_is_not_a_duplicate(
    registration_client: TestClient,
    agent_sessions: sessionmaker[Session],
) -> None:
    response = registration_client.post("/api/auth/register", json=REGISTRATION)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "database_error"
    _assert_no_internals(response.json())
    assert _user_count(agent_sessions, REGISTRATION["email"]) == 0


def test_unrelated_integrity_failure_is_not_a_duplicate(
    fk_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "allow_public_registration", True)

    @event.listens_for(fk_sessions, "before_flush")
    def dangling_organization(session: Session, *_: object) -> None:
        for obj in session.new:
            if isinstance(obj, User):
                obj.organization_id = 999999

    try:
        with _client_for(fk_sessions) as client:
            response = client.post("/api/auth/register", json=REGISTRATION)
    finally:
        app.dependency_overrides.pop(get_db, None)
        event.remove(fk_sessions, "before_flush", dangling_organization)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "database_error"
    _assert_no_internals(response.json())
    assert _user_count(fk_sessions, REGISTRATION["email"]) == 0


def test_failed_recheck_returns_generic_error(
    registration_client: TestClient,
    agent_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _email_exists_sequence(
        monkeypatch, False, OperationalError("SELECT secret", {}, Exception("users table"))
    )

    response = registration_client.post("/api/auth/register", json=REGISTRATION)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "database_error"
    _assert_no_internals(response.json())
    assert _user_count(agent_sessions, REGISTRATION["email"]) == 0


def test_registration_errors_expose_no_internals(
    registration_client: TestClient,
    agent_sessions: sessionmaker[Session],
) -> None:
    _seed(agent_sessions, "taken@example.com")
    duplicate = registration_client.post(
        "/api/auth/register", json={**REGISTRATION, "email": "taken@example.com"}
    )
    failure = registration_client.post("/api/auth/register", json=REGISTRATION)

    assert (duplicate.status_code, failure.status_code) == (409, 500)
    for response in (duplicate, failure):
        _assert_no_internals(response.json())


# --- migration module ---------------------------------------------------------------


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location(
        "migration_0012", ROOT / "alembic" / "versions" / "20261013_0012_add_organizations.py"
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_migration_refuses_non_mysql_databases() -> None:
    migration = _load_migration()

    assert (migration.revision, migration.down_revision) == ("20261013_0012", "20261013_0011")
    assert len(migration.STEPS) == 8
    engine = create_engine("sqlite://")
    with engine.connect() as conn, pytest.raises(migration.MigrationStopped, match="MySQL only"):
        migration.run_steps(conn, through=8)


def test_membership_validators_reject_invisible_index_and_foreign_schema_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    good_index = {"ix_users_organization_id": (1, "organization_id", "YES")}
    good_key = [("fk_users_organization_id_organizations", "organizations", "organization_id", "RESTRICT", 1)]

    monkeypatch.setattr(migration, "_membership_indexes", lambda _: good_index)
    assert migration._validate_index(None) is True
    monkeypatch.setattr(
        migration, "_membership_indexes",
        lambda _: {"ix_users_organization_id": (1, "organization_id", "NO")},
    )
    with pytest.raises(migration.MigrationStopped):
        migration._validate_index(None)

    monkeypatch.setattr(migration, "_membership_foreign_keys", lambda _: good_key)
    assert migration._validate_foreign_key(None) is True
    monkeypatch.setattr(
        migration, "_membership_foreign_keys", lambda _: [good_key[0][:4] + (0,)]
    )
    with pytest.raises(migration.MigrationStopped):
        migration._validate_foreign_key(None)


class _RecordingOp:
    """Stands in for alembic.op so downgrade's skip logic runs without MySQL."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_context(self) -> Any:
        return type("Context", (), {"as_sql": False})()

    def get_bind(self) -> None:
        return None

    def drop_column(self, _table: str, name: str) -> None:
        self.calls.append(("drop_column", name))

    def drop_constraint(self, name: str, _table: str, **_: object) -> None:
        self.calls.append(("drop_constraint", name))

    def drop_index(self, name: str, **_: object) -> None:
        self.calls.append(("drop_index", name))

    def drop_table(self, name: str) -> None:
        self.calls.append(("drop_table", name))


FULL_DOWNGRADE = [
    ("drop_column", "email_verified_at"),
    ("drop_column", "verification_grace_until"),
    ("drop_constraint", "fk_users_organization_id_organizations"),
    ("drop_index", "ix_users_organization_id"),
    ("drop_column", "organization_id"),
    ("drop_table", "organizations"),
]


def _run_downgrade(
    monkeypatch: pytest.MonkeyPatch,
    *,
    organizations: bool = True,
    membership: bool = True,
    verification_missing: tuple[str, ...] = (),
    foreign_key: bool = True,
    index: bool = True,
    references: list[tuple[Any, ...]] | None = None,
    legacy_row_ok: bool = True,
) -> list[tuple[str, str]]:
    """Run downgrade() against a described schema state; the schema validators are stubbed."""
    migration = _load_migration()
    recorder = _RecordingOp()

    def legacy_row(_: object) -> None:
        if not legacy_row_ok:
            migration._stop("organizations must contain exactly the legacy workspace row.")

    stubs = {
        "op": recorder,
        "_table_exists": lambda _, table: organizations if table == "organizations" else True,
        "_columns": lambda _, __: {"organization_id": {}} if membership else {},
        "_validate_legacy_row": legacy_row,
        "_organization_references": lambda _: (
            [(1, "users", "fk_users_organization_id_organizations")] if foreign_key else []
        ) if references is None else references,
        "_validate_membership_column": lambda _, **__: None,
        "_validate_membership_data": lambda _: None,
        "_validate_verification_columns": lambda _, **__: list(verification_missing),
        "_validate_foreign_key": lambda _: foreign_key,
        "_validate_index": lambda _: index,
    }
    for name, value in stubs.items():
        monkeypatch.setattr(migration, name, value)
    try:
        migration.downgrade()
    except migration.MigrationStopped:
        assert recorder.calls == [], "nothing may be dropped once a check fails"
        raise
    return recorder.calls


def test_downgrade_drops_everything_from_a_complete_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _run_downgrade(monkeypatch) == FULL_DOWNGRADE


@pytest.mark.parametrize(
    ("state", "remaining"),
    [
        ({"verification_missing": ("email_verified_at",)}, FULL_DOWNGRADE[1:]),
        (
            {"verification_missing": ("email_verified_at", "verification_grace_until"),
             "foreign_key": False},
            FULL_DOWNGRADE[3:],
        ),
        (
            {"verification_missing": ("email_verified_at", "verification_grace_until"),
             "foreign_key": False, "index": False},
            FULL_DOWNGRADE[4:],
        ),
        (
            {"verification_missing": ("email_verified_at", "verification_grace_until"),
             "foreign_key": False, "index": False, "membership": False},
            FULL_DOWNGRADE[5:],
        ),
    ],
)
def test_downgrade_resumes_after_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, Any],
    remaining: list[tuple[str, str]],
) -> None:
    assert _run_downgrade(monkeypatch, **state) == remaining


@pytest.mark.parametrize(
    "state",
    [
        {"legacy_row_ok": False},
        {"references": [(1, "users", "fk_users_organization_id_organizations"),
                        (1, "invites", "fk_invites_organization")]},
        {"references": [(0, "users", "fk_users_organization_id_organizations")]},
        {"organizations": False},
    ],
    ids=["extra-organization", "another-table-references", "other-schema-reference", "table-gone-first"],
)
def test_downgrade_refuses_before_dropping_anything(
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, Any],
) -> None:
    migration_stopped = _load_migration().MigrationStopped.__name__
    with pytest.raises(Exception) as excinfo:
        _run_downgrade(monkeypatch, **state)
    assert type(excinfo.value).__name__ == migration_stopped
