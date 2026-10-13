"""Scope CRM records and action policies to organizations.

Adds organization_id to every organization-owned table, backfills existing rows to
the legacy workspace, then enforces it: NOT NULL, organization-scoped uniqueness,
and composite foreign keys so a customer can only reference a company, and an
activity only a customer, of its own organization.

MySQL commits DDL implicitly, so a failed run can leave earlier steps applied while
alembic_version stays at 20261013_0012. Every step therefore checks what already
exists, validates it exactly, and either resumes or stops; it never overwrites
state it does not recognise. Run it in a maintenance window: NOT NULL and the new
indexes rebuild each table, and writes to it wait while that happens.

Revision ID: 20261013_0013
Revises: 20261013_0012
Create Date: 2026-10-13
"""

from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261013_0013"
down_revision: Union[str, None] = "20261013_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Literal copies: a migration must not change if application constants do.
LEGACY_NAME = "Default Workspace"
LEGACY_SLUG = "default-workspace"
CHILD_TABLES = ("contacts", "sales_enquiries", "meetings", "calls", "follow_ups")
SCOPED_TABLES = (
    "companies",
    "customers",
    *CHILD_TABLES,
    "hitl_policy_overrides",
    "hitl_policy_audits",
)
OLD_COMPANY_NAME_UNIQUE = "uq_companies_company_name"

# name -> (table, unique, columns)
INDEXES: dict[str, tuple[str, bool, tuple[str, ...]]] = {
    "uq_companies_organization_company": ("companies", True, ("organization_id", "company_id")),
    "uq_companies_organization_company_name": (
        "companies", True, ("organization_id", "company_name"),
    ),
    "uq_customers_organization_customer": ("customers", True, ("organization_id", "customer_id")),
    "ix_customers_organization_company": ("customers", False, ("organization_id", "company_id")),
    **{
        f"ix_{table}_organization_customer": (table, False, ("organization_id", "customer_id"))
        for table in CHILD_TABLES
    },
    "ix_hitl_policy_audits_organization_changed": (
        "hitl_policy_audits", False, ("organization_id", "changed_at"),
    ),
}

# name -> (table, columns, referenced table, referenced columns); all ON DELETE RESTRICT.
FOREIGN_KEYS: dict[str, tuple[str, tuple[str, ...], str, tuple[str, ...]]] = {
    "fk_companies_organization_id_organizations": (
        "companies", ("organization_id",), "organizations", ("organization_id",),
    ),
    "fk_customers_company_organization": (
        "customers", ("organization_id", "company_id"),
        "companies", ("organization_id", "company_id"),
    ),
    **{
        f"fk_{table}_customer_organization": (
            table, ("organization_id", "customer_id"),
            "customers", ("organization_id", "customer_id"),
        )
        for table in CHILD_TABLES
    },
    "fk_hitl_policy_overrides_organization_id_organizations": (
        "hitl_policy_overrides", ("organization_id",), "organizations", ("organization_id",),
    ),
    "fk_hitl_policy_audits_organization_id_organizations": (
        "hitl_policy_audits", ("organization_id",), "organizations", ("organization_id",),
    ),
}

OLD_POLICY_KEY = ("action_type",)
NEW_POLICY_KEY = ("organization_id", "action_type")


class MigrationStopped(RuntimeError):
    pass


def _stop(reason: str) -> None:
    raise MigrationStopped(
        f"Migration 20261013_0013 stopped: {reason} "
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


def _column(bind: Any, table: str, column: str) -> tuple[str, str, Any] | None:
    row = bind.execute(
        sa.text(
            "SELECT COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return None if row is None else (row[0].lower(), row[1], row[2])


def _indexes(bind: Any, table: str) -> dict[str, tuple[bool, tuple[str, ...], str]]:
    rows = bind.execute(
        sa.text(
            "SELECT INDEX_NAME, MAX(NON_UNIQUE), "
            "GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX), MIN(IS_VISIBLE) "
            "FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :t GROUP BY INDEX_NAME"
        ),
        {"t": table},
    ).all()
    return {
        name: (int(non_unique) == 0, tuple(columns.split(",")), visible)
        for name, non_unique, columns, visible in rows
    }


def _foreign_keys(bind: Any, table: str) -> dict[str, tuple[Any, ...]]:
    rows = bind.execute(
        sa.text(
            "SELECT k.CONSTRAINT_NAME, "
            "GROUP_CONCAT(k.COLUMN_NAME ORDER BY k.ORDINAL_POSITION), "
            "MIN(k.REFERENCED_TABLE_NAME), "
            "GROUP_CONCAT(k.REFERENCED_COLUMN_NAME ORDER BY k.ORDINAL_POSITION), "
            "MIN(r.DELETE_RULE), MIN(k.REFERENCED_TABLE_SCHEMA = DATABASE()) "
            "FROM information_schema.key_column_usage k "
            "JOIN information_schema.referential_constraints r "
            "ON r.constraint_schema = k.constraint_schema "
            "AND r.constraint_name = k.constraint_name "
            "WHERE k.table_schema = DATABASE() AND k.table_name = :t "
            "GROUP BY k.CONSTRAINT_NAME"
        ),
        {"t": table},
    ).all()
    return {
        name: (tuple(columns.split(",")), ref_table, tuple(ref_columns.split(",")), rule, int(same))
        for name, columns, ref_table, ref_columns, rule, same in rows
    }


def _primary_key(bind: Any, table: str) -> tuple[str, ...]:
    return _indexes(bind, table).get("PRIMARY", (True, (), "YES"))[1]


def _legacy_id(bind: Any) -> int:
    rows = bind.execute(sa.text("SELECT organization_id, name, slug, status FROM organizations")).all()
    if [tuple(row[1:]) for row in rows] != [(LEGACY_NAME, LEGACY_SLUG, "active")]:
        _stop("organizations must contain exactly the legacy workspace row.")
    return int(rows[0][0])


def _row_counts(bind: Any) -> dict[str, int]:
    return {
        table: int(_scalar(bind, f"SELECT COUNT(*) FROM `{table}`"))
        for table in (*SCOPED_TABLES, "users", "organizations")
    }


# --- validators -----------------------------------------------------------------


def _validate_column(bind: Any, table: str, *, nullable: set[str]) -> None:
    column = _column(bind, table, "organization_id")
    if column is None:
        _stop(f"{table}.organization_id is missing.")
    column_type, is_nullable, default = column
    if column_type != "int" or is_nullable not in nullable or default is not None:
        _stop(f"{table}.organization_id is {column_type} nullable={is_nullable} default={default}.")


def _validate_data(bind: Any, legacy_id: int) -> None:
    for table in SCOPED_TABLES:
        if _scalar(bind, f"SELECT COUNT(*) FROM `{table}` WHERE organization_id IS NULL"):
            _stop(f"{table} has rows without an organization.")
        if _scalar(
            bind, f"SELECT COUNT(*) FROM `{table}` WHERE organization_id <> :id", id=legacy_id
        ):
            _stop(f"{table} has rows outside the legacy workspace.")


def _has_index(bind: Any, name: str) -> bool:
    """True when the index exists exactly as expected; stops on any other definition."""
    table, unique, columns = INDEXES[name]
    found = _indexes(bind, table).get(name)
    if found is None:
        return False
    # An invisible index is ignored by the optimiser, so it does not count as present.
    if found != (unique, columns, "YES"):
        _stop(f"{table} index {name} is {found}, expected {(unique, columns, 'YES')}.")
    return True


def _has_foreign_key(bind: Any, name: str) -> bool:
    """True when the key exists exactly as expected; stops on any other definition."""
    table, columns, ref_table, ref_columns = FOREIGN_KEYS[name]
    found = _foreign_keys(bind, table).get(name)
    if found is None:
        return False
    expected = (columns, ref_table, ref_columns, "RESTRICT", 1)
    if found != expected:
        _stop(f"{table} foreign key {name} is {found}, expected {expected}.")
    return True


def _validate_policy_key(bind: Any, allowed: set[tuple[str, ...]]) -> tuple[str, ...]:
    key = _primary_key(bind, "hitl_policy_overrides")
    if key not in allowed:
        _stop(f"hitl_policy_overrides has primary key {key}.")
    return key


def _validate_old_company_name_unique(bind: Any) -> bool:
    found = _indexes(bind, "companies").get(OLD_COMPANY_NAME_UNIQUE)
    if found is None:
        return False
    if found != (True, ("company_name",), "YES"):
        _stop(f"{OLD_COMPANY_NAME_UNIQUE} has an unexpected definition {found}.")
    return True


# --- steps ----------------------------------------------------------------------


def _step_0_preconditions(bind: Any) -> dict[str, int]:
    if bind.dialect.name != "mysql":
        _stop(f"this migration supports MySQL only, not {bind.dialect.name}.")
    versions = [row[0] for row in bind.execute(sa.text("SELECT version_num FROM alembic_version"))]
    if versions != ["20261013_0012"]:
        _stop(f"expected alembic_version 20261013_0012, found {versions}.")
    for table in (*SCOPED_TABLES, "organizations", "users", "scheduling_locks"):
        if not _table_exists(bind, table):
            _stop(f"the {table} table is missing.")
    legacy_id = _legacy_id(bind)
    # Every row is backfilled to the legacy workspace, which is only correct while it
    # is the sole tenant: no user may belong anywhere else.
    if _scalar(
        bind,
        "SELECT COUNT(*) FROM users WHERE organization_id IS NULL OR organization_id <> :id",
        id=legacy_id,
    ):
        _stop("some users are outside the legacy workspace.")
    for table in SCOPED_TABLES:
        if _column(bind, table, "organization_id") is not None:
            _validate_column(bind, table, nullable={"YES", "NO"})
    # The backfill must not touch timestamps; an ON UPDATE column would rewrite them.
    auto_updated = _scalar(
        bind,
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND LOWER(EXTRA) LIKE '%on update%' "
        "AND table_name IN (" + ", ".join(f"'{table}'" for table in SCOPED_TABLES) + ")",
    )
    if auto_updated:
        _stop("a scoped table has an ON UPDATE column; the backfill would change timestamps.")
    return _row_counts(bind)


def _step_1_add_columns(bind: Any) -> None:
    for table in SCOPED_TABLES:
        if _column(bind, table, "organization_id") is None:
            op.add_column(table, sa.Column("organization_id", sa.Integer(), nullable=True))
        _validate_column(bind, table, nullable={"YES", "NO"})


def _step_2_backfill(bind: Any) -> None:
    legacy_id = _legacy_id(bind)
    for table in SCOPED_TABLES:
        if _scalar(
            bind,
            f"SELECT COUNT(*) FROM `{table}` "
            "WHERE organization_id IS NOT NULL AND organization_id <> :id",
            id=legacy_id,
        ):
            _stop(f"{table} already has rows in another organization; not overwriting.")
        bind.execute(
            sa.text(f"UPDATE `{table}` SET organization_id = :id WHERE organization_id IS NULL"),
            {"id": legacy_id},
        )


def _step_3_validate_backfill(bind: Any) -> None:
    _validate_data(bind, _legacy_id(bind))


def _step_4_require_organization(bind: Any) -> None:
    for table in SCOPED_TABLES:
        if _column(bind, table, "organization_id")[1] == "YES":
            # existing_type is required: MySQL MODIFY rewrites the whole column definition.
            op.alter_column(
                table, "organization_id", existing_type=sa.Integer(), existing_nullable=True,
                nullable=False,
            )
        _validate_column(bind, table, nullable={"NO"})


def _step_5_indexes(bind: Any) -> None:
    # Before the foreign keys, so MySQL does not add its own index under another name.
    for name, (table, unique, columns) in INDEXES.items():
        if _has_index(bind, name):
            continue
        if unique:
            op.create_unique_constraint(name, table, list(columns))
        else:
            op.create_index(name, table, list(columns))
        if not _has_index(bind, name):
            _stop(f"index {name} was not created as expected.")


def _step_6_drop_global_company_name_unique(bind: Any) -> None:
    # Only after the organization-scoped replacement exists, so names are never unguarded.
    if not _has_index(bind, "uq_companies_organization_company_name"):
        _stop("the organization-scoped company name constraint is missing.")
    if _validate_old_company_name_unique(bind):
        op.drop_constraint(OLD_COMPANY_NAME_UNIQUE, "companies", type_="unique")


def _step_7_policy_primary_key(bind: Any) -> None:
    if _validate_policy_key(bind, {OLD_POLICY_KEY, NEW_POLICY_KEY}) == OLD_POLICY_KEY:
        # One statement, so the table is never without a primary key.
        op.execute(
            "ALTER TABLE hitl_policy_overrides "
            "DROP PRIMARY KEY, ADD PRIMARY KEY (organization_id, action_type)"
        )
    _validate_policy_key(bind, {NEW_POLICY_KEY})


def _step_8_foreign_keys(bind: Any) -> None:
    for name, (table, columns, ref_table, ref_columns) in FOREIGN_KEYS.items():
        if _has_foreign_key(bind, name):
            continue
        op.create_foreign_key(
            name, table, ref_table, list(columns), list(ref_columns), ondelete="RESTRICT"
        )
        if not _has_foreign_key(bind, name):
            _stop(f"foreign key {name} was not created as expected.")


def _step_9_scheduling_lock(bind: Any) -> None:
    # The scheduling lock is per organization: its lock_id is the organization_id.
    bind.execute(
        sa.text(
            "INSERT INTO scheduling_locks (lock_id) SELECT :id FROM DUAL "
            "WHERE NOT EXISTS (SELECT 1 FROM scheduling_locks WHERE lock_id = :id)"
        ),
        {"id": _legacy_id(bind)},
    )


def _step_10_final_validation(bind: Any, snapshot: dict[str, int]) -> None:
    legacy_id = _legacy_id(bind)
    for table in SCOPED_TABLES:
        _validate_column(bind, table, nullable={"NO"})
    _validate_data(bind, legacy_id)
    for name in INDEXES:
        if not _has_index(bind, name):
            _stop(f"index {name} is missing.")
    if _validate_old_company_name_unique(bind):
        _stop(f"{OLD_COMPANY_NAME_UNIQUE} still exists.")
    _validate_policy_key(bind, {NEW_POLICY_KEY})
    for name in FOREIGN_KEYS:
        if not _has_foreign_key(bind, name):
            _stop(f"foreign key {name} is missing.")
    if not _scalar(bind, "SELECT COUNT(*) FROM scheduling_locks WHERE lock_id = :id", id=legacy_id):
        _stop("the legacy workspace has no scheduling lock row.")
    counts = _row_counts(bind)
    if counts != snapshot:
        _stop(f"row counts changed during the migration: {snapshot} -> {counts}.")


STEPS = (
    _step_1_add_columns,
    _step_2_backfill,
    _step_3_validate_backfill,
    _step_4_require_organization,
    _step_5_indexes,
    _step_6_drop_global_company_name_unique,
    _step_7_policy_primary_key,
    _step_8_foreign_keys,
    _step_9_scheduling_lock,
)


def run_steps(bind: Any, through: int) -> dict[str, int]:
    """Run steps 0..through (1-9). Used by upgrade() and interruption tests."""
    snapshot = _step_0_preconditions(bind)
    for step in STEPS[:through]:
        step(bind)
    return snapshot


def upgrade() -> None:
    if op.get_context().as_sql:
        _stop("offline (--sql) mode is not supported; run against a live database.")
    bind = op.get_bind()
    snapshot = run_steps(bind, through=len(STEPS))
    _step_10_final_validation(bind, snapshot)


def downgrade() -> None:
    """Resumable, and refused once data exists outside the legacy workspace.

    With a second organization, global company-name uniqueness and the global policy
    key could not be restored, and tenants' records would become indistinguishable.
    """
    if op.get_context().as_sql:
        _stop("offline (--sql) mode is not supported; run against a live database.")
    bind = op.get_bind()
    legacy_id = _legacy_id(bind)
    present = [t for t in SCOPED_TABLES if _column(bind, t, "organization_id") is not None]
    for table in present:
        _validate_column(bind, table, nullable={"YES", "NO"})
        if _scalar(
            bind,
            f"SELECT COUNT(*) FROM `{table}` WHERE organization_id <> :id",
            id=legacy_id,
        ):
            _stop(f"{table} has rows outside the legacy workspace; downgrade would merge tenants.")
    # Validate everything that remains before dropping anything.
    foreign_keys = [
        name for name, spec in FOREIGN_KEYS.items()
        if spec[0] in present and _has_foreign_key(bind, name)
    ]
    indexes = [
        name for name, spec in INDEXES.items() if spec[0] in present and _has_index(bind, name)
    ]
    has_old_unique = _validate_old_company_name_unique(bind)
    policy_key = _validate_policy_key(bind, {OLD_POLICY_KEY, NEW_POLICY_KEY})

    for name in foreign_keys:
        op.drop_constraint(name, FOREIGN_KEYS[name][0], type_="foreignkey")
    if not has_old_unique:
        op.create_unique_constraint(OLD_COMPANY_NAME_UNIQUE, "companies", ["company_name"])
    if policy_key == NEW_POLICY_KEY:
        op.execute(
            "ALTER TABLE hitl_policy_overrides DROP PRIMARY KEY, ADD PRIMARY KEY (action_type)"
        )
    for name in indexes:
        table, unique, _ = INDEXES[name]
        if unique:
            op.drop_constraint(name, table, type_="unique")
        else:
            op.drop_index(name, table_name=table)
    for table in present:
        op.drop_column(table, "organization_id")
    # The legacy scheduling lock row is kept: 20261013_0012 code ignores extra rows.
