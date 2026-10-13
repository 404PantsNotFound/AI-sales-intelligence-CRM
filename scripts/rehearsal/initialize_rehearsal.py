"""Rehearsal only: build an empty ai_sales_crm_rehearsal schema at 20261013_0011.

Never reads, dumps or copies ai_sales_crm. The schema is created empty, built by the
real Alembic revisions (not metadata.create_all), filled with a few clearly synthetic
users, and only then marked with _rehearsal_marker so migration_interrupt.py accepts it.

Inputs (credentials only via the environment, never arguments or output):
  REHEARSAL_ADMIN_URL     Local MySQL administrator, with no default database, e.g.
                          mysql+pymysql://root:<pw>@127.0.0.1:3306
                          Used only to CREATE DATABASE, CREATE USER and GRANT.
  REHEARSAL_DATABASE_URL  The limited account this script creates and the harness uses,
                          mysql+pymysql://rehearsal_<name>:<pw>@127.0.0.1:3306/ai_sales_crm_rehearsal

Usage, from the repository root (the app settings read .env from the working directory):
  python scripts/rehearsal/initialize_rehearsal.py \
      --target ai_sales_crm_rehearsal --confirm-host 127.0.0.1:3306

It never drops or overwrites anything: if the schema or the account already exists,
it stops before changing anything.
"""

import argparse
import importlib.util
import os
import re
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Connection, make_url

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "migration_interrupt", Path(__file__).with_name("migration_interrupt.py")
)
assert _spec is not None and _spec.loader is not None
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

GuardError = harness.GuardError
REHEARSAL_DATABASE = harness.REHEARSAL_DATABASE
STARTING_REVISION = harness.STARTING_REVISION
MARKER_TABLE = harness.MARKER_TABLE
REQUIRED_TARGET = ("127.0.0.1", 3306)
ACCOUNT_NAME = re.compile(r"^rehearsal_[a-z0-9_]{1,22}$")
MIN_PASSWORD_LENGTH = 16
# Exactly what migrations 0001-0012 and their downgrades need, on this schema only.
ACCOUNT_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES"
# Tables 0011 must have produced; organizations must not exist yet.
EXPECTED_TABLES = {
    "alembic_version", "users", "companies", "customers", "contacts", "sales_enquiries",
    "meetings", "calls", "follow_ups", "scheduling_locks", "hitl_tasks",
    "hitl_action_proposals", "hitl_approval_decisions", "hitl_execution_audits",
    "hitl_policy_overrides", "hitl_policy_audits", "hitl_action_audits",
    "google_calendar_connections", "calendar_event_links", "agent_conversations",
    "agent_conversation_messages",
}
USER_COLUMNS_AT_0011 = {
    "user_id", "email", "full_name", "password_hash", "role", "is_active",
    "created_at", "updated_at",
}
# Synthetic only: .invalid is a reserved TLD (RFC 2606) and the hash is not a valid
# Argon2 hash, so no row can receive mail or be logged into. Mixed roles and an
# inactive user exercise the migration's role-count and membership checks.
SYNTHETIC_USERS = (
    ("rehearsal-sales-1@example.invalid", "Rehearsal Sales One", "sales", True),
    ("rehearsal-sales-2@example.invalid", "Rehearsal Sales Two", "sales", True),
    ("rehearsal-admin-1@example.invalid", "Rehearsal Admin One", "admin", True),
    ("rehearsal-inactive-1@example.invalid", "Rehearsal Inactive One", "sales", False),
)
UNUSABLE_PASSWORD_HASH = "!rehearsal-synthetic-not-a-password-hash"


def _app_database_user(environ: dict[str, str], dotenv_path: Path) -> str | None:
    """The account the application uses, read the way app settings would read it."""
    if environ.get("DB_USER"):
        return environ["DB_USER"]
    if dotenv_path.is_file():
        from dotenv import dotenv_values

        return dotenv_values(dotenv_path).get("DB_USER") or None
    return None


def validate_inputs(
    *,
    target: str,
    confirm_host: str,
    environ: dict[str, str],
    app_user: str | None,
) -> tuple[URL, URL]:
    """Every check that needs no connection. Returns (admin_url, rehearsal_url)."""
    if target != REHEARSAL_DATABASE:
        raise GuardError(f"--target must be exactly {REHEARSAL_DATABASE}.")

    # Database name, host:port allowlist, query options and ENVIRONMENT, as the harness checks.
    rehearsal_url = harness.target_url(environ)
    rehearsal_target = (rehearsal_url.host or "", rehearsal_url.port or harness.DEFAULT_PORT)
    # The account is created for this host, and an IP literal matches without name
    # resolution; 'localhost' would depend on skip_name_resolve and reverse DNS.
    if rehearsal_target != REQUIRED_TARGET:
        raise GuardError(
            f"the rehearsal URL must use {REQUIRED_TARGET[0]}:{REQUIRED_TARGET[1]} exactly."
        )
    if confirm_host != f"{rehearsal_target[0]}:{rehearsal_target[1]}":
        raise GuardError(
            f"--confirm-host must repeat the rehearsal target {rehearsal_target[0]}:{rehearsal_target[1]}."
        )

    raw_admin = environ.get("REHEARSAL_ADMIN_URL", "").strip()
    if not raw_admin:
        raise GuardError("REHEARSAL_ADMIN_URL is not set.")
    try:
        admin_url = make_url(raw_admin)
    except Exception as exc:
        raise GuardError("REHEARSAL_ADMIN_URL is not a valid URL.") from exc
    if not admin_url.drivername.startswith("mysql"):
        raise GuardError("the administrator connection must be MySQL.")
    if admin_url.database:
        raise GuardError("REHEARSAL_ADMIN_URL must not name a database; it only creates one.")
    if set(admin_url.query) - harness.ALLOWED_QUERY_KEYS:
        raise GuardError("only these URL query options are allowed: charset.")
    if (admin_url.host or "", admin_url.port or harness.DEFAULT_PORT) != rehearsal_target:
        raise GuardError("the administrator and rehearsal connections must use the same host and port.")

    admin_user = admin_url.username or ""
    rehearsal_user = rehearsal_url.username or ""
    if not admin_user:
        raise GuardError("REHEARSAL_ADMIN_URL must name an administrator account.")
    if not ACCOUNT_NAME.fullmatch(rehearsal_user):
        raise GuardError("the rehearsal account name must match rehearsal_[a-z0-9_]{1,22}.")
    if len(rehearsal_url.password or "") < MIN_PASSWORD_LENGTH:
        raise GuardError(f"the rehearsal account password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if app_user and app_user.lower() in {admin_user.lower(), rehearsal_user.lower()}:
        raise GuardError("the application's database account must not be used for the rehearsal.")
    if admin_user.lower() == rehearsal_user.lower():
        raise GuardError("the administrator and rehearsal accounts must be different.")
    return admin_url, rehearsal_url


def admin_statements(rehearsal_url: URL) -> list[tuple[str, dict[str, str]]]:
    """The only statements the administrator connection runs. None may name another schema."""
    account = {"user": rehearsal_url.username or "", "host": rehearsal_url.host or ""}
    return [
        # No IF NOT EXISTS: an existing schema makes this fail instead of being reused.
        (
            f"CREATE DATABASE `{REHEARSAL_DATABASE}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
            {},
        ),
        ("CREATE USER :user@:host IDENTIFIED BY :password", {**account, "password": rehearsal_url.password or ""}),
        (f"GRANT {ACCOUNT_PRIVILEGES} ON `{REHEARSAL_DATABASE}`.* TO :user@:host", account),
    ]


def _refuse_existing(admin: Connection, rehearsal_url: URL) -> None:
    schema_exists = admin.scalar(
        text("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = :name"),
        {"name": REHEARSAL_DATABASE},
    )
    if schema_exists:
        raise GuardError(
            f"{REHEARSAL_DATABASE} already exists, so nothing was changed. Inspect it with "
            "`migration_interrupt.py show`; to start again, an administrator must review it and "
            f"run DROP DATABASE `{REHEARSAL_DATABASE}` manually."
        )
    account_exists = admin.scalar(
        text("SELECT COUNT(*) FROM mysql.user WHERE user = :user AND host = :host"),
        {"user": rehearsal_url.username, "host": rehearsal_url.host},
    )
    if account_exists:
        raise GuardError(
            "the rehearsal account already exists, so nothing was changed. Review its grants "
            "with SHOW GRANTS, or choose another rehearsal_ account name."
        )


def create_schema_and_account(admin_url: URL, rehearsal_url: URL) -> None:
    engine = create_engine(admin_url)
    try:
        with engine.connect() as admin:
            if admin.scalar(text("SELECT DATABASE()")) is not None:
                raise GuardError("the administrator connection has a default database.")
            _refuse_existing(admin, rehearsal_url)
            try:
                for statement, params in admin_statements(rehearsal_url):
                    admin.execute(text(statement), params)
                admin.commit()
            except Exception as exc:
                # DDL commits as it goes, so the schema may exist without the account.
                raise GuardError(
                    f"creating {REHEARSAL_DATABASE} or its account failed partway "
                    f"({type(exc).__name__}). Nothing was dropped; an administrator must "
                    "inspect and remove whatever was created before retrying."
                ) from exc
    finally:
        engine.dispose()


def build_schema_to_0011() -> None:
    """Real migration history, connected as the limited account via the app settings."""
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), STARTING_REVISION)


def verify_schema(conn: Connection) -> None:
    if conn.scalar(text("SELECT DATABASE()")) != REHEARSAL_DATABASE:
        raise GuardError("the connected database is not the rehearsal database.")
    versions = [row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))]
    if versions != [STARTING_REVISION]:
        raise GuardError(f"expected alembic_version {STARTING_REVISION}, found {versions}.")
    tables = {
        row[0]
        for row in conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()")
        )
    }
    if tables != EXPECTED_TABLES:
        raise GuardError(
            f"unexpected tables after migrating: missing {sorted(EXPECTED_TABLES - tables)}, "
            f"extra {sorted(tables - EXPECTED_TABLES)}."
        )
    columns = {
        row[0]
        for row in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'users'"
            )
        )
    }
    if columns != USER_COLUMNS_AT_0011:
        raise GuardError(f"users does not match revision {STARTING_REVISION}: {sorted(columns)}.")
    if conn.scalar(text("SELECT COUNT(*) FROM users")):
        raise GuardError("users is not empty in a freshly created schema.")


def seed_and_mark(conn: Connection) -> None:
    for email, full_name, role, is_active in SYNTHETIC_USERS:
        conn.execute(
            text(
                "INSERT INTO users (email, full_name, password_hash, role, is_active) "
                "VALUES (:email, :full_name, :password_hash, :role, :is_active)"
            ),
            {
                "email": email,
                "full_name": full_name,
                "password_hash": UNUSABLE_PASSWORD_HASH,
                "role": role,
                "is_active": is_active,
            },
        )
    conn.commit()
    # The marker comes last, after the schema, revision and data are confirmed.
    conn.execute(
        text(
            f"CREATE TABLE `{MARKER_TABLE}` ("
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "note VARCHAR(100) NOT NULL)"
        )
    )
    conn.execute(
        text(f"INSERT INTO `{MARKER_TABLE}` (note) VALUES (:note)"),
        {"note": "synthetic schema from initialize_rehearsal.py"},
    )
    conn.commit()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", required=True, help=f"must be exactly {REHEARSAL_DATABASE}")
    parser.add_argument("--confirm-host", required=True, help="host:port, e.g. 127.0.0.1:3306")
    args = parser.parse_args(argv)

    admin_url, rehearsal_url = validate_inputs(
        target=args.target,
        confirm_host=args.confirm_host,
        environ=dict(os.environ),
        app_user=_app_database_user(dict(os.environ), Path.cwd() / ".env"),
    )
    # Aims and checks the app settings (including a production .env) before any connection.
    harness.point_app_settings_at(rehearsal_url)
    print("Target:", rehearsal_url.render_as_string(hide_password=True))

    create_schema_and_account(admin_url, rehearsal_url)
    try:
        build_schema_to_0011()
        engine = create_engine(rehearsal_url)
        try:
            with engine.connect() as conn:
                verify_schema(conn)
                seed_and_mark(conn)
                harness.check_connection(conn, revision=STARTING_REVISION)
        finally:
            engine.dispose()
    except BaseException:
        print(
            f"Initialization stopped after {REHEARSAL_DATABASE} and its account were created. "
            "Nothing was dropped. Inspect the schema, then an administrator may remove it "
            "manually (DROP DATABASE and DROP USER) before retrying."
        )
        raise

    print(
        f"{REHEARSAL_DATABASE} is at {STARTING_REVISION} with {len(SYNTHETIC_USERS)} synthetic "
        "users and the rehearsal marker. Keep REHEARSAL_DATABASE_URL set for migration_interrupt.py."
    )


if __name__ == "__main__":
    main()
