# Runbook: migration 20261013_0013 (organization-scoped CRM)

Do not run any step against production until a rehearsal on a restored copy has
passed every check below and the run has been explicitly authorized.

## What the migration does

- Adds `organization_id` to `companies`, `customers`, `contacts`, `sales_enquiries`,
  `meetings`, `calls`, `follow_ups`, `hitl_policy_overrides`, `hitl_policy_audits`.
- Backfills every existing row to the `default-workspace` organization, then makes the
  column `NOT NULL`.
- Replaces the global `uq_companies_company_name` with `(organization_id, company_name)`.
- Adds composite foreign keys (`organization_id`, parent id) so a customer can only
  reference its own organization's company and an activity only its own organization's
  customer, plus `(organization_id, …)` indexes.
- Changes the `hitl_policy_overrides` primary key to `(organization_id, action_type)`.
- Ensures a `scheduling_locks` row whose `lock_id` is the legacy organization's ID.

IDs, links and timestamps are not changed; the migration stops if any row count changes.

**The code and the schema must change together.** The new code requires the new
columns, and code from `2e60767` cannot insert rows into the migrated schema. Stop the
application before migrating, and only start the new code afterwards.

## 1. Backup (and prove it restores)

1. Stop the application, so no writes happen during the window.
2. Write a **new, dated** dump. Never overwrite `ai_sales_crm_backup.sql`, which is outdated:
   `mysqldump --single-transaction --routines --triggers --set-gtid-purged=OFF -u <admin> -p ai_sales_crm > ai_sales_crm_2026xxxx_pre0013.sql`
3. Restore it into an empty rehearsal schema (`ai_sales_crm_rehearsal`), created by an
   admin account, and compare its row counts with production's (preflight query P4).
   A dump that has not been restored is not a backup.

## 2. Rehearsal on the restored copy

1. Point the application settings at the rehearsal schema through process environment
   variables (these override `.env`; do not edit `.env`):
   `DB_HOST=127.0.0.1 DB_PORT=3306 DB_NAME=ai_sales_crm_rehearsal DB_USER=<rehearsal user> DB_PASSWORD=<…>`
2. Confirm the target before anything else: `SELECT DATABASE();` returns `ai_sales_crm_rehearsal`.
3. Run the preflight queries (section 3) and record the results.
4. `python -m alembic upgrade head`, then run the post-migration checks (section 5).
5. Rehearse an interruption: restore again, stop the upgrade partway (kill the process
   after the first `ALTER TABLE`), rerun `python -m alembic upgrade head`, and confirm it
   resumes and the section 5 checks pass.
6. Rehearse rollback: `python -m alembic downgrade 20261013_0012` must succeed and
   restore `uq_companies_company_name`.
7. Optionally run the automated interruption and downgrade tests against the disposable
   test schema: `MYSQL_TEST_URL=mysql+pymysql://<user>:<pw>@127.0.0.1:3306/ai_sales_crm_test python -m pytest tests/test_organization_migration.py tests/test_scheduling_mysql.py`
   (they drop every table in `ai_sales_crm_test` and refuse any other database).

## 3. Preflight (read-only; run on production during the window, before migrating)

```sql
-- P1 revision: expect exactly 20261013_0012
SELECT version_num FROM alembic_version;
-- P2 organizations: expect exactly one row, 'Default Workspace' / 'default-workspace' / 'active'
SELECT organization_id, name, slug, status FROM organizations;
SET @legacy := (SELECT organization_id FROM organizations WHERE slug = 'default-workspace');
-- P3 every user in the legacy workspace: expect 0
SELECT COUNT(*) FROM users WHERE organization_id IS NULL OR organization_id <> @legacy;
-- P4 row counts: record them; they must be identical after the migration
SELECT 'companies', COUNT(*) FROM companies UNION ALL SELECT 'customers', COUNT(*) FROM customers
UNION ALL SELECT 'contacts', COUNT(*) FROM contacts UNION ALL SELECT 'sales_enquiries', COUNT(*) FROM sales_enquiries
UNION ALL SELECT 'meetings', COUNT(*) FROM meetings UNION ALL SELECT 'calls', COUNT(*) FROM calls
UNION ALL SELECT 'follow_ups', COUNT(*) FROM follow_ups
UNION ALL SELECT 'hitl_policy_overrides', COUNT(*) FROM hitl_policy_overrides
UNION ALL SELECT 'hitl_policy_audits', COUNT(*) FROM hitl_policy_audits;
-- P5 no organization_id yet on the scoped tables: expect 0 rows (otherwise a run was interrupted; see section 4)
SELECT table_name FROM information_schema.columns WHERE table_schema = DATABASE()
  AND column_name = 'organization_id' AND table_name NOT IN ('users', 'organizations');
-- P6 no auto-updating timestamps (the backfill must not touch updated_at): expect 0
SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE()
  AND LOWER(EXTRA) LIKE '%on update%';
-- P7 table sizes, to size the window (each scoped table is rebuilt)
SELECT table_name, table_rows, ROUND((data_length + index_length) / 1048576, 1) AS mb
FROM information_schema.tables WHERE table_schema = DATABASE() ORDER BY mb DESC;
-- P8 nothing else connected or holding locks: expect only this session
SHOW PROCESSLIST;
```

Stop and investigate if any result differs from what is expected.

## 4. Run, and recovery from an interruption

- `python -m alembic upgrade head`, with the application stopped.
- MySQL commits each DDL statement, so an interrupted run leaves some steps applied
  while `alembic_version` stays `20261013_0012`. **Rerun the same command**: every step
  checks what exists and resumes. Never edit `alembic_version` by hand.
- If it stops with `Migration 20261013_0013 stopped: …`, nothing unexpected was
  overwritten. Read the reason, fix the cause, and rerun. If the cause is unclear,
  restore the section 1 dump.
- `--sql` (offline) mode is refused by design.

## 5. Post-migration verification (read-only)

```sql
-- In one session; @legacy is set as in P2.
SELECT version_num FROM alembic_version;                       -- 20261013_0013
-- Same counts as P4:
-- (rerun the P4 query)
-- Nothing outside the legacy workspace, nothing NULL: expect 0 for each table
SELECT COUNT(*) FROM customers WHERE organization_id IS NULL OR organization_id <> @legacy;
-- Columns are NOT NULL: expect 9 rows, all IS_NULLABLE = 'NO'
SELECT table_name, is_nullable FROM information_schema.columns WHERE table_schema = DATABASE()
  AND column_name = 'organization_id' AND table_name NOT IN ('users', 'organizations');
-- Foreign keys: expect 10 rows (9 new + fk_users_organization_id_organizations), all RESTRICT
SELECT constraint_name, table_name, delete_rule FROM information_schema.referential_constraints
WHERE constraint_schema = DATABASE() AND constraint_name LIKE 'fk\_%organization%';
-- Global company-name constraint gone: expect 0
SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE()
  AND index_name = 'uq_companies_company_name';
-- Per-organization scheduling lock: expect 1
SELECT COUNT(*) FROM scheduling_locks WHERE lock_id = @legacy;
```

Then deploy and start the new code, sign in, and check that the customer list,
a customer overview, analytics, the scheduling calendar and an AI chat still work.

## 6. Rollback

- Schema: `python -m alembic downgrade 20261013_0012`. It refuses once any data
  belongs to a second organization, because tenants would be merged. Then deploy
  `2e60767`.
- If the downgrade refuses or fails: stop the application and restore the section 1 dump.

## Before a second organization exists

Keep `ALLOW_PUBLIC_REGISTRATION=false` until signup (Phase 2) is implemented. That
signup must create the organization and its `scheduling_locks` row (`lock_id` =
`organization_id`) in one transaction; without the row, scheduling for that
organization fails safely with `scheduling_not_ready`.
