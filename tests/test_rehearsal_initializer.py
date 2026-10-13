"""Safety guards of the rehearsal initializer. Nothing here connects to a database."""

import importlib.util
import re
from pathlib import Path

import pytest

from app.core.security import verify_password
from app.database.connection import Base

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rehearsal" / "initialize_rehearsal.py"
spec = importlib.util.spec_from_file_location("initialize_rehearsal", SCRIPT)
assert spec is not None and spec.loader is not None
initializer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(initializer)

REHEARSAL = "mysql+pymysql://rehearsal_ops:a-long-rehearsal-password@127.0.0.1:3306/ai_sales_crm_rehearsal"
ADMIN = "mysql+pymysql://root:admin-password@127.0.0.1:3306"
VALID = {
    "target": "ai_sales_crm_rehearsal",
    "confirm_host": "127.0.0.1:3306",
    "environ": {"REHEARSAL_DATABASE_URL": REHEARSAL, "REHEARSAL_ADMIN_URL": ADMIN},
    "app_user": "crm_app",
}


def _with(**changes: object) -> dict[str, object]:
    inputs = {**VALID, **changes}
    if "env" in changes:
        inputs["environ"] = {**VALID["environ"], **changes["env"]}  # type: ignore[dict-item]
        del inputs["env"]
    return inputs


def test_accepts_explicit_local_rehearsal_inputs() -> None:
    admin_url, rehearsal_url = initializer.validate_inputs(**VALID)
    assert admin_url.database is None
    assert rehearsal_url.database == "ai_sales_crm_rehearsal"


@pytest.mark.parametrize(
    ("inputs", "reason"),
    [
        (_with(target="ai_sales_crm"), "--target must be exactly"),
        (_with(target="AI_SALES_CRM_REHEARSAL"), "--target must be exactly"),
        (_with(confirm_host="localhost:3306"), "--confirm-host"),
        (
            _with(env={"REHEARSAL_DATABASE_URL": REHEARSAL.replace("127.0.0.1", "localhost"),
                       "REHEARSAL_ADMIN_URL": ADMIN.replace("127.0.0.1", "localhost")},
                  confirm_host="localhost:3306"),
            "must use 127.0.0.1:3306 exactly",
        ),
        (
            _with(env={"REHEARSAL_DATABASE_URL": REHEARSAL.replace("127.0.0.1", "[::1]"),
                       "REHEARSAL_ADMIN_URL": ADMIN.replace("127.0.0.1", "[::1]")},
                  confirm_host="::1:3306"),
            "must use 127.0.0.1:3306 exactly",
        ),
        (_with(env={"REHEARSAL_DATABASE_URL": REHEARSAL.replace("127.0.0.1", "10.0.0.5")}), "allowlist"),
        (_with(env={"REHEARSAL_DATABASE_URL": REHEARSAL.replace("_rehearsal", "")}), "must be exactly"),
        (_with(env={"REHEARSAL_DATABASE_URL": REHEARSAL.replace(":3306", ":3307")}), "allowlist"),
        (_with(env={"ENVIRONMENT": "production"}), "production"),
        (_with(env={"REHEARSAL_ADMIN_URL": ""}), "REHEARSAL_ADMIN_URL is not set"),
        (_with(env={"REHEARSAL_ADMIN_URL": ADMIN + "/ai_sales_crm"}), "must not name a database"),
        (_with(env={"REHEARSAL_ADMIN_URL": ADMIN + "?init_command=USE+ai_sales_crm"}), "query options"),
        (_with(env={"REHEARSAL_ADMIN_URL": ADMIN.replace(":3306", ":3307")}), "same host and port"),
        (_with(env={"REHEARSAL_ADMIN_URL": "sqlite:///admin.db"}), "must be MySQL"),
        (
            _with(env={"REHEARSAL_DATABASE_URL": REHEARSAL.replace("rehearsal_ops", "crm_app")}),
            "rehearsal account name",
        ),
        (
            _with(env={"REHEARSAL_DATABASE_URL": REHEARSAL.replace("a-long-rehearsal-password", "short")}),
            "at least 16",
        ),
        (_with(app_user="root"), "application's database account"),
        (_with(app_user="rehearsal_ops"), "application's database account"),
        (
            _with(env={"REHEARSAL_ADMIN_URL": ADMIN.replace("root:", "rehearsal_ops:")}),
            "must be different",
        ),
    ],
)
def test_refuses_unsafe_inputs(inputs: dict[str, object], reason: str) -> None:
    with pytest.raises(initializer.GuardError, match=reason):
        initializer.validate_inputs(**inputs)


def test_admin_statements_touch_only_the_rehearsal_schema() -> None:
    _, rehearsal_url = initializer.validate_inputs(**VALID)
    statements = initializer.admin_statements(rehearsal_url)

    for sql, params in statements:
        assert set(re.findall(r"`([^`]+)`", sql)) <= {"ai_sales_crm_rehearsal"}
        # The DROP *privilege* is granted (the guarded downgrade drops a table); no
        # statement here may itself drop, reuse an existing schema or switch schemas.
        assert not re.match(r"\s*(DROP|USE|ALTER)\b", sql, re.IGNORECASE)
        assert not re.search(r"\b(IF NOT EXISTS|ALL PRIVILEGES|\*\.\*)", sql, re.IGNORECASE)
        assert "a-long-rehearsal-password" not in sql  # bound, never interpolated
    grant = statements[-1][0]
    assert grant.startswith("GRANT ") and "ON `ai_sales_crm_rehearsal`.*" in grant
    assert "CREATE DATABASE `ai_sales_crm_rehearsal`" in statements[0][0]


def test_synthetic_users_cannot_log_in_or_receive_mail() -> None:
    for email, full_name, _role, _active in initializer.SYNTHETIC_USERS:
        assert email.endswith("@example.invalid")
        assert full_name.startswith("Rehearsal ")
    assert not verify_password("anything", initializer.UNUSABLE_PASSWORD_HASH)
    assert not initializer.UNUSABLE_PASSWORD_HASH.startswith("$argon2")


def test_expected_0011_schema_matches_models_without_phase_1() -> None:
    model_tables = set(Base.metadata.tables) - {"organizations"}
    assert initializer.EXPECTED_TABLES == model_tables | {"alembic_version"}
    user_columns = set(Base.metadata.tables["users"].columns.keys())
    phase_1 = {"organization_id", "email_verified_at", "verification_grace_until"}
    assert initializer.USER_COLUMNS_AT_0011 == user_columns - phase_1


def test_reads_the_application_account_from_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DB_USER=crm_app\nDB_PASSWORD=not-read\n", encoding="utf-8")
    assert initializer._app_database_user({}, tmp_path / ".env") == "crm_app"
    assert initializer._app_database_user({"DB_USER": "env_app"}, tmp_path / ".env") == "env_app"
    assert initializer._app_database_user({}, tmp_path / "missing.env") is None


VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _upgrade_chain_through(head: str) -> list[str]:
    """Upgrade() sources from the first revision through `head`, following down_revision."""
    revisions: dict[str, tuple[str | None, str]] = {}
    for path in VERSIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r'^revision: str = "([^"]+)"', source, re.MULTILINE)
        down = re.search(r'^down_revision: [^=]+= (?:"([^"]+)"|None)', source, re.MULTILINE)
        assert revision and down, path.name
        upgrade = source.split("\ndef downgrade", 1)[0]
        revisions[revision.group(1)] = (down.group(1), upgrade)

    chain: list[str] = []
    current: str | None = head
    while current is not None:
        down, upgrade = revisions[current]
        chain.append(upgrade)
        current = down
    return chain[::-1]


def test_expected_0011_tables_match_migration_history() -> None:
    chain = _upgrade_chain_through("20261013_0011")
    assert len(chain) == 11

    created: set[str] = set()
    for upgrade in chain:
        created |= set(re.findall(r'op\.create_table\(\s*"([a-z_]+)"', upgrade))
        # A dropped or renamed table would make a plain union of create_table calls wrong.
        assert not re.search(r"op\.(drop_table|rename_table)\(", upgrade)

    # alembic_version is created by Alembic itself, not by any revision.
    assert initializer.EXPECTED_TABLES == created | {"alembic_version"}
    assert "organizations" not in created
