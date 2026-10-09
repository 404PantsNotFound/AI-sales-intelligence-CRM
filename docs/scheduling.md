# Shared activity scheduling

Meetings, scheduled calls, and active follow-ups share one CRM-wide calendar.
Intervals use UTC instants and half-open bounds (`[start, end)`), so one
activity may begin exactly when another ends. Cancelled/completed meetings,
non-scheduled calls, and completed/cancelled follow-ups do not reserve time.
Only calls with a `scheduled_at` value reserve a slot.

Durations use the activity's stored duration when present. Otherwise the
application applies and persists these configurable defaults for new records:

| Activity | Default duration |
| --- | ---: |
| Meeting | 60 minutes |
| Scheduled call | 30 minutes |
| Pending, in-progress, or overdue follow-up | 15 minutes |

Existing records without a duration use the same configured default at
validation time. Operators should avoid changing these settings without
considering existing undurationed records.

The default suggestion window is weekdays, 09:00–17:00 in the request's IANA
timezone, in 15-minute increments, with up to five suggestions over 14 days.
All values are configurable with the `SCHEDULING_*` environment settings in
`app/core/config.py`. Alternatives are searched strictly after the requested
instant, deduplicated, and checked against all active calendar intervals. DST
gaps and ambiguous wall-clock times are skipped in suggestions rather than
shifted or assigned an arbitrary occurrence. Requested meeting times retain
the existing explicit DST clarification behavior.

The AI proposal tools and structured HITL tasks check availability before
creating an approval proposal. Conflicting HITL task input remains saved in its
collecting state; the response includes available alternatives, which the user
must explicitly select and submit before a proposal is created. Approval-time
execution still rechecks authoritatively under the shared scheduling lock.
Agents and manual activity forms likewise keep the requested time unchanged
until a user chooses an alternative.

Activities on the workspace Overview can be dragged to another day or moved
with the card's date control. The move endpoint keeps the activity's wall-clock
time as displayed in the calendar's timezone and retains its saved timezone
label and duration. When the calendar timezone differs from the saved activity
timezone, the saved-local wall-clock time can therefore change. The endpoint
uses the normal update service to acquire the workspace schedule lock and
reject overlaps before committing to the database. The board reloads from the
persisted workspace schedule only after the update succeeds. If the target
local time falls in a DST gap, the move is rejected; for an ambiguous
fall-back time, the original occurrence preference is retained. Completed,
cancelled, non-scheduled calls, and calls without a scheduled time cannot be
moved.

Workbook call and follow-up timestamps may be supplied as timezone-aware
instants or as local wall-clock datetimes with `scheduled_timezone` or
`due_timezone`. Naive values without a timezone label retain the legacy UTC
interpretation. A nonexistent DST time is rejected; an ambiguous time requires
`scheduled_time_occurrence` or `due_time_occurrence` set to `earlier` or `later`.

The `scheduling_locks` singleton row serializes schedule-changing transactions
across the shared workspace. Every write path must acquire it before locking
activity/reference rows, validate availability, and keep it until transaction
commit or rollback. Migration `20261009_0009` creates and seeds that row and
adds the call/follow-up timezone and duration columns used by the ORM. The app
process may start without this revision, but call/follow-up ORM operations,
schedule-locked writes, and `schedule_call` proposals require it. Apply the
migration before deploying the new application code against an existing
database.
Availability checks are advisory; create/update/import operations perform the
authoritative recheck while holding the lock. This lock serializes application
writers but does not protect against external processes that write activity
tables without using the application protocol.
MySQL serialization follows InnoDB row-locking semantics: application writes
lock the seeded singleton row with `SELECT ... FOR UPDATE` and retain that lock
through activity validation and commit. SQLite-based unit tests do not verify
that MySQL concurrency behavior; no disposable MySQL integration database is
configured by this test suite.

The migration downgrade drops the saved call/follow-up timezone labels and
durations. It also restores the prior HITL action-type constraint; downgrade
will fail if `schedule_call` proposals remain. Review those persisted proposals
before planning a downgrade rather than deleting audit or approval records.
