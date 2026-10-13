"""Add organizations and assign every existing user to the legacy workspace.

MySQL commits DDL implicitly, so a failed run can leave earlier steps applied
while alembic_version stays at 20261013_0011. Every step therefore validates
the actual definition and data of anything already present and either resumes
or stops; it never overwrites state it does not recognise.

Revision ID: 20261013_0012
Revises: 20261013_0011
Create Date: 2026-10-13
"""

from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261013_0012"
down_revision: Union[str, None] = "20261013_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Literal copies: a migration must not change if application constants do.
LEGACY_NAME = "Default Workspace"
LEGACY_SLUG = "default-workspace"
FK_NAME = "fk_users_organization_id_organizations"
INDEX_NAME = "ix_users_organization_id"
UNIQUE_NAME = "uq_organizations_slug"
CHECK_NAME = "ck_organizations_status"
VERIFICATION_COLUMNS = ("email_verified_at", "verification_grace_until")
USER_COLUMNS = {
    "user_id", "email", "full_name", "password_hash", "role", "is_active",
    "created_at", "updated_at",
}


class MigrationStopped(RuntimeError):
    pass


def _stop(reason: str) -> None:
    raise MigrationStopped(
        f"Migration 20261013_0012 stopped: {reason} "
        "Investigate, or restore the verified backup, before retrying."
    )


def _scalar(bind: Any, sql: str, **params: Any) -> Any:
    return bind.execute(sa.text(sql), params).scalar()


def _table_exists(bind: Any, table: str) -> bool:
    return bool(
        _scalar(
            bind,
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = :t",
            t=table,
        )
    )


def _columns(bind: Any, table: str) -> dict[str, dict[str, Any]]:
    rows = bind.execute(
        sa.text(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA "
            "FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t"
        ),
        {"t": table},
    ).all()
    return {
        row[0]: {"type": row[1].lower(), "nullable": row[2], "default": row[3], "extra": row[4]}
        for row in rows
    }


def _legacy_id(bind: Any) -> int:
    legacy_id = _scalar(
        bind, "SELECT organization_id FROM organizations WHERE slug = :s", s=LEGACY_SLUG
    )
    if legacy_id is None:
        _stop("the legacy organization row is missing.")
    return int(legacy_id)


def _user_snapshot(bind: Any) -> dict[str, int]:
    rows = bind.execute(sa.text("SELECT role, COUNT(*) FROM users GROUP BY role")).all()
    snapshot = {str(role): int(count) for role, count in rows}
    snapshot["__total__"] = int(_scalar(bind, "SELECT COUNT(*) FROM users"))
    return snapshot


# --- validators -----------------------------------------------------------------


def _validate_organizations_table(bind: Any) -> None:
    columns = _columns(bind, "organizations")
    expected = {
        "organization_id": ("int", "NO"),
        "name": ("varchar(255)", "NO"),
        "slug": ("varchar(63)", "NO"),
        "status": ("varchar(16)", "NO"),
        "created_at": ("datetime", "NO"),
        "updated_at": ("datetime", "NO"),
    }
    if set(columns) != set(expected):
        _stop(f"organizations has unexpected columns {sorted(columns)}.")
    for name, (column_type, nullable) in expected.items():
        actual = columns[name]
        if actual["type"] != column_type or actual["nullable"] != nullable:
            _stop(f"organizations.{name} is {actual['type']} nullable={actual['nullable']}.")
    if "auto_increment" not in (columns["organization_id"]["extra"] or "").lower():
        _stop("organizations.organization_id is not auto-increment.")
    if columns["status"]["default"] != "active":
        _stop("organizations.status does not default to 'active'.")
    for name in ("name", "slug"):
        if columns[name]["default"] is not None:
            _stop(f"organizations.{name} has an unexpected default.")
    for name in ("created_at", "updated_at"):
        if columns[name]["default"] is None:
            _stop(f"organizations.{name} has no server default.")

    if _scalar(
        bind,
        "SELECT engine FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = 'organizations'",
    ) != "InnoDB":
        _stop("organizations is not an InnoDB table.")

    constraints = bind.execute(
        sa.text(
            "SELECT tc.CONSTRAINT_NAME, tc.CONSTRAINT_TYPE, tc.ENFORCED, "
            "GROUP_CONCAT(k.COLUMN_NAME ORDER BY k.ORDINAL_POSITION) "
            "FROM information_schema.table_constraints tc "
            "LEFT JOIN information_schema.key_column_usage k "
            "ON k.constraint_schema = tc.constraint_schema "
            "AND k.table_name = tc.table_name AND k.constraint_name = tc.constraint_name "
            "WHERE tc.table_schema = DATABASE() AND tc.table_name = 'organizations' "
            "GROUP BY tc.CONSTRAINT_NAME, tc.CONSTRAINT_TYPE, tc.ENFORCED"
        )
    ).all()
    found = {(name, kind, enforced): cols for name, kind, enforced, cols in constraints}
    # ENFORCED must be YES: a NOT ENFORCED check would let invalid statuses in.
    expected_constraints = {
        ("PRIMARY", "PRIMARY KEY", "YES"): "organization_id",
        (UNIQUE_NAME, "UNIQUE", "YES"): "slug",
        (CHECK_NAME, "CHECK", "YES"): None,
    }
    if found != expected_constraints:
        _stop(f"organizations has unexpected constraints {sorted(found)}.")

    clause = (
        _scalar(
            bind,
            "SELECT CHECK_CLAUSE FROM information_schema.check_constraints "
            "WHERE constraint_schema = DATABASE() AND constraint_name = :n",
            n=CHECK_NAME,
        )
        or ""
    ).lower().replace("\\", "")  # information_schema may escape the quotes.
    if not all(part in clause for part in ("status", "'active'", "'suspended'")) or clause.count(
        "'"
    ) != 4:
        _stop(f"{CHECK_NAME} has an unexpected definition.")


def _validate_legacy_row(bind: Any) -> None:
    rows = bind.execute(sa.text("SELECT name, slug, status FROM organizations")).all()
    if [tuple(row) for row in rows] != [(LEGACY_NAME, LEGACY_SLUG, "active")]:
        _stop("organizations must contain exactly the legacy workspace row.")


def _validate_membership_column(bind: Any, *, nullable: set[str]) -> None:
    column = _columns(bind, "users").get("organization_id")
    if column is None:
        _stop("users.organization_id is missing.")
    if column["type"] != "int" or column["nullable"] not in nullable or column["default"] is not None:
        _stop(
            f"users.organization_id is {column['type']} nullable={column['nullable']} "
            f"default={column['default']}."
        )


def _validate_membership_data(bind: Any) -> None:
    legacy_id = _legacy_id(bind)
    if _scalar(bind, "SELECT COUNT(*) FROM users WHERE organization_id IS NULL"):
        _stop("some users have no organization.")
    if _scalar(
        bind, "SELECT COUNT(*) FROM users WHERE organization_id <> :id", id=legacy_id
    ):
        _stop("some users belong to an organization other than the legacy workspace.")


def _membership_indexes(bind: Any) -> dict[str, tuple[int, str, str]]:
    rows = bind.execute(
        sa.text(
            "SELECT INDEX_NAME, MAX(NON_UNIQUE), GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX), "
            "MIN(IS_VISIBLE) "
            "FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = 'users' "
            "GROUP BY INDEX_NAME "
            "HAVING SUM(COLUMN_NAME = 'organization_id') > 0"
        )
    ).all()
    return {
        name: (int(non_unique), columns, visible)
        for name, non_unique, columns, visible in rows
    }


def _membership_foreign_keys(bind: Any) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in bind.execute(
            sa.text(
                "SELECT k.CONSTRAINT_NAME, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME, "
                "r.DELETE_RULE, k.REFERENCED_TABLE_SCHEMA = DATABASE() "
                "FROM information_schema.key_column_usage k "
                "JOIN information_schema.referential_constraints r "
                "ON r.constraint_schema = k.constraint_schema "
                "AND r.constraint_name = k.constraint_name "
                "WHERE k.table_schema = DATABASE() AND k.table_name = 'users' "
                "AND k.column_name = 'organization_id'"
            )
        ).all()
    ]


def _organization_references(bind: Any) -> list[tuple[Any, ...]]:
    """Every foreign key, in any schema, that points at this schema's organizations."""
    return [
        tuple(row)
        for row in bind.execute(
            sa.text(
                "SELECT table_schema = DATABASE(), table_name, constraint_name "
                "FROM information_schema.key_column_usage "
                "WHERE referenced_table_schema = DATABASE() "
                "AND referenced_table_name = 'organizations'"
            )
        ).all()
    ]


def _validate_index(bind: Any) -> bool:
    """True when the expected index exists; stops on any other index over the column."""
    indexes = _membership_indexes(bind)
    if not indexes:
        return False
    # An invisible index is ignored by the optimiser, so it does not count as present.
    if indexes != {INDEX_NAME: (1, "organization_id", "YES")}:
        _stop(f"users has unexpected indexes over organization_id {indexes}.")
    return True


def _validate_foreign_key(bind: Any) -> bool:
    """True when the expected foreign key exists; stops on any other one."""
    foreign_keys = _membership_foreign_keys(bind)
    if not foreign_keys:
        return False
    if foreign_keys != [(FK_NAME, "organizations", "organization_id", "RESTRICT", 1)]:
        _stop(f"users.organization_id has unexpected foreign keys {foreign_keys}.")
    return True


def _validate_verification_columns(bind: Any, *, required: bool) -> list[str]:
    """Return the verification columns still missing; stop on any unexpected one."""
    columns = _columns(bind, "users")
    missing = []
    for name in VERIFICATION_COLUMNS:
        column = columns.get(name)
        if column is None:
            if required:
                _stop(f"users.{name} is missing.")
            missing.append(name)
            continue
        if column["type"] != "datetime" or column["nullable"] != "YES" or column["default"] is not None:
            _stop(f"users.{name} is {column['type']} nullable={column['nullable']}.")
        if _scalar(bind, f"SELECT COUNT(*) FROM users WHERE {name} IS NOT NULL"):
            _stop(f"users.{name} already holds values; Phase 1 never populates it.")
    return missing


# --- steps ----------------------------------------------------------------------


def _step_0_preconditions(bind: Any) -> dict[str, int]:
    if bind.dialect.name != "mysql":
        _stop(f"this migration supports MySQL only, not {bind.dialect.name}.")
    versions = [row[0] for row in bind.execute(sa.text("SELECT version_num FROM alembic_version"))]
    if versions != ["20261013_0011"]:
        _stop(f"expected alembic_version 20261013_0011, found {versions}.")
    if not _table_exists(bind, "users"):
        _stop("the users table is missing.")
    columns = set(_columns(bind, "users"))
    unexpected = columns - USER_COLUMNS - {"organization_id", *VERIFICATION_COLUMNS}
    if not USER_COLUMNS <= columns or unexpected:
        _stop(f"users has unexpected columns {sorted(columns)}.")
    if columns - USER_COLUMNS and not _table_exists(bind, "organizations"):
        _stop("users has Phase 1 columns but the organizations table is missing.")
    return _user_snapshot(bind)


def _step_1_create_organizations(bind: Any) -> None:
    if not _table_exists(bind, "organizations"):
        op.create_table(
            "organizations",
            sa.Column("organization_id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("slug", sa.String(length=63), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("slug", name=UNIQUE_NAME),
            sa.CheckConstraint("status IN ('active', 'suspended')", name=CHECK_NAME),
            mysql_engine="InnoDB",
        )
    _validate_organizations_table(bind)


def _step_2_legacy_organization(bind: Any) -> None:
    if not _scalar(bind, "SELECT COUNT(*) FROM organizations"):
        bind.execute(
            sa.text(
                "INSERT INTO organizations (name, slug, status) "
                "SELECT :name, :slug, 'active' FROM DUAL "
                "WHERE NOT EXISTS (SELECT 1 FROM organizations WHERE slug = :slug)"
            ),
            {"name": LEGACY_NAME, "slug": LEGACY_SLUG},
        )
    _validate_legacy_row(bind)


def _step_3_add_membership_column(bind: Any) -> None:
    if "organization_id" not in _columns(bind, "users"):
        op.add_column("users", sa.Column("organization_id", sa.Integer(), nullable=True))
    _validate_membership_column(bind, nullable={"YES", "NO"})


def _step_4_backfill(bind: Any) -> None:
    legacy_id = _legacy_id(bind)
    if _scalar(
        bind,
        "SELECT COUNT(*) FROM users WHERE organization_id IS NOT NULL AND organization_id <> :id",
        id=legacy_id,
    ):
        _stop("some users already belong to a non-legacy organization; not overwriting.")
    bind.execute(
        sa.text("UPDATE users SET organization_id = :id WHERE organization_id IS NULL"),
        {"id": legacy_id},
    )


def _step_5_validate_backfill(bind: Any) -> None:
    _validate_membership_data(bind)
    if _scalar(
        bind,
        "SELECT COUNT(*) FROM users u LEFT JOIN organizations o "
        "ON o.organization_id = u.organization_id WHERE o.organization_id IS NULL",
    ):
        _stop("some users reference a missing organization.")


def _step_6_index_and_foreign_key(bind: Any) -> None:
    # The index comes first so MySQL does not create one under another name for the key.
    if not _validate_index(bind):
        if _membership_foreign_keys(bind):
            _stop("a foreign key exists on users.organization_id without the expected index.")
        op.create_index(INDEX_NAME, "users", ["organization_id"])
        _validate_index(bind)
    if not _validate_foreign_key(bind):
        op.create_foreign_key(
            FK_NAME, "users", "organizations", ["organization_id"], ["organization_id"],
            ondelete="RESTRICT",
        )
        if not _validate_foreign_key(bind):
            _stop("the foreign key was not created as expected.")


def _step_7_require_membership(bind: Any) -> None:
    if _columns(bind, "users")["organization_id"]["nullable"] == "YES":
        # existing_type is required: MySQL MODIFY rewrites the whole column definition.
        op.alter_column(
            "users", "organization_id", existing_type=sa.Integer(), existing_nullable=True,
            nullable=False,
        )
    _validate_membership_column(bind, nullable={"NO"})


def _step_8_add_verification_columns(bind: Any) -> None:
    for name in _validate_verification_columns(bind, required=False):
        op.add_column("users", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    _validate_verification_columns(bind, required=True)


def _step_9_final_validation(bind: Any, snapshot: dict[str, int]) -> None:
    _validate_organizations_table(bind)
    _validate_legacy_row(bind)
    _validate_membership_column(bind, nullable={"NO"})
    _step_5_validate_backfill(bind)
    if not _validate_index(bind) or not _validate_foreign_key(bind):
        _stop("the membership index or foreign key is missing.")
    _validate_verification_columns(bind, required=True)
    if _user_snapshot(bind) != snapshot:
        _stop("user or role counts changed during the migration.")


STEPS = (
    _step_1_create_organizations,
    _step_2_legacy_organization,
    _step_3_add_membership_column,
    _step_4_backfill,
    _step_5_validate_backfill,
    _step_6_index_and_foreign_key,
    _step_7_require_membership,
    _step_8_add_verification_columns,
)


def run_steps(bind: Any, through: int) -> dict[str, int]:
    """Run steps 0..through (1-8). Used by upgrade() and the rehearsal harness."""
    snapshot = _step_0_preconditions(bind)
    for step in STEPS[:through]:
        step(bind)
    return snapshot


def upgrade() -> None:
    if op.get_context().as_sql:
        _stop("offline (--sql) mode is not supported; run against a live database.")
    bind = op.get_bind()
    snapshot = run_steps(bind, through=len(STEPS))
    _step_9_final_validation(bind, snapshot)


def downgrade() -> None:
    """Resumable: MySQL commits each drop, so a rerun skips drops already done.

    Everything that remains is validated before anything is dropped, so a rerun
    never discards data the original checks would have protected.
    """
    if op.get_context().as_sql:
        _stop("offline (--sql) mode is not supported; run against a live database.")
    bind = op.get_bind()
    has_organizations = _table_exists(bind, "organizations")
    has_membership = "organization_id" in _columns(bind, "users")

    if has_membership and not has_organizations:
        _stop("users.organization_id exists but the organizations table is missing.")
    if has_organizations:
        _validate_legacy_row(bind)
        references = _organization_references(bind)
        if references not in ([], [(1, "users", FK_NAME)]):
            _stop(f"other tables reference organizations {references}; downgrade would lose data.")
    if has_membership:
        _validate_membership_column(bind, nullable={"YES", "NO"})
        _validate_membership_data(bind)
    # Validates the verification columns still present (type, and all values NULL).
    missing_verification = _validate_verification_columns(bind, required=False)
    has_foreign_key = has_membership and _validate_foreign_key(bind)
    has_index = has_membership and _validate_index(bind)

    for name in VERIFICATION_COLUMNS:
        if name not in missing_verification:
            op.drop_column("users", name)
    if has_foreign_key:
        op.drop_constraint(FK_NAME, "users", type_="foreignkey")
    if has_index:
        op.drop_index(INDEX_NAME, table_name="users")
    if has_membership:
        op.drop_column("users", "organization_id")
    if has_organizations:
        op.drop_table("organizations")
