# AI Sales CRM

A FastAPI sales CRM with SQLAlchemy-backed customer and activity workflows,
a vanilla HTML/CSS/JavaScript interface, and a read-only LangChain assistant.

## Requirements

- Python 3.10+
- `pip`

## Setup

Create and activate a virtual environment, then install the dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set environment variables as needed. The
configuration supports `APP_NAME`, `ENVIRONMENT`, `API_PREFIX`,
`CORS_ALLOWED_ORIGINS`, database settings, and optional `GEMINI_API_KEY` and
`GEMINI_MODEL` settings. Gemini 2.5 Flash is the default model. Do not commit
real credentials.

## Run

From the project root:

```powershell
uvicorn app.main:app --reload
```

The service is available at `http://127.0.0.1:8000`. Verify it with
`GET /health`, which returns:

```json
{
  "status": "ok"
}
```

FastAPI's interactive API documentation is available at `/docs`. API module
test routes are available under `/api/{module}/test` for customers, enquiries,
contacts, meetings, calls, followups, analytics, and agent.

## Project status

This repository provides the FastAPI backend, SQLAlchemy database foundation
and migration, customer registration and retrieval, customer activity
workflows, and a read-only LangChain sales assistant. The customer
intelligence interface is served by the same FastAPI application.

## Database setup and migrations

1. Create the database manually in MySQL if it does not already exist.
2. Set `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD` in your
   local `.env` file. Do not commit that file.
3. Apply the checked-in schema migration from the project root:

   ```powershell
   alembic upgrade head
   ```

   To inspect generated SQL without connecting to MySQL:

   ```powershell
   alembic upgrade head --sql
   ```

Application startup does not open a database connection. Database access is
opened on demand through `get_db()`; `check_database_connection()` is an
explicit connectivity check. Alembic migrations, rather than
`Base.metadata.create_all()`, manage the schema.

Run the local, database-independent test suite with:

```powershell
python -m pytest -q
```

Company and customer deletion are restricted while related rows exist. CRM
relationships do not configure ORM delete cascades. Optional references from
meetings, calls, and follow-ups use `ON DELETE SET NULL`, retaining their
historical records if a referenced optional contact, enquiry, meeting, or call
is deleted.


## Customer registration API

`POST /api/customers` registers a company (reusing a case-insensitive,
trimmed-name match when present), customer, primary contact, and initial sales
enquiry in a single transaction. The request groups those data as
`customer_name`, `status`, and `sales_stage`, plus `company`,
`primary_contact`, and `sales_enquiry` objects. For example:

```json
{
  "customer_name": "Ada Lovelace",
  "status": "active",
  "sales_stage": "qualified",
  "company": {"company_name": "Analytical Engines Ltd"},
  "primary_contact": {"name": "Ada Lovelace", "email": "ada@example.com"},
  "sales_enquiry": {
    "enquiry_text": "Interested in a team subscription.",
    "priority": "high",
    "status": "open",
    "estimated_value": "1250.50"
  }
}
```

Allowed customer statuses are `active`, `inactive`, and `prospect`; sales
stages are `new`, `qualified`, `proposal`, `negotiation`, `won`, and `lost`;
enquiry priorities are `low`, `normal`, `high`, and `urgent`; enquiry statuses
are `open`, `in_progress`, `converted`, `closed`, and `lost`.
If omitted, customer status/sales stage default to `active`/`new`, and enquiry
priority/status default to `normal`/`open`; product, estimated value, and
non-required company/contact details remain optional.

Customer retrieval is available at `GET /api/customers/{customer_id}`. List
customers with `GET /api/customers?page=1&page_size=20`; `search` searches
customer and company names, while `customer_name` and `company_name` filter
those fields individually. `page_size` is limited to 100.

Customer API tests use an isolated in-memory SQLite database and do not access
the configured MySQL database.

## Customer activity API

Meetings, calls, and follow-ups are created and updated through services that
validate the customer and any optional contact, enquiry, meeting, or call
references. Each write is performed in a single SQLAlchemy transaction.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/meetings` | Schedule a meeting |
| `GET` | `/api/meetings/{meeting_id}` | Retrieve a meeting |
| `PUT` | `/api/meetings/{meeting_id}` | Update a meeting |
| `GET` | `/api/customers/{customer_id}/meetings` | List a customer's meetings |
| `POST` | `/api/calls` | Log a call |
| `GET` | `/api/calls/{call_id}` | Retrieve a call |
| `PUT` | `/api/calls/{call_id}` | Update a call |
| `GET` | `/api/customers/{customer_id}/calls` | List a customer's calls |
| `POST` | `/api/followups` | Create a follow-up |
| `GET` | `/api/followups/{followup_id}` | Retrieve a follow-up |
| `PUT` | `/api/followups/{followup_id}` | Update a follow-up |
| `GET` | `/api/customers/{customer_id}/followups` | List a customer's follow-ups |
| `GET` | `/api/customers/{customer_id}/activity` | Get the combined timeline |
| `GET` | `/api/customers/{customer_id}/overview` | Get the customer and activity overview |

The activity endpoint accepts optional `type` (`enquiry`, `meeting`, `call`,
or `follow_up`), `start_date`, and `end_date` query parameters. Date bounds
are inclusive; a start date after the end date returns a validation error.
Meeting statuses are `scheduled`, `completed`, `cancelled`, and `no_show`;
call statuses are `scheduled`, `attempted`, `completed`, `failed`, and
`cancelled`; follow-up statuses are `pending`, `in_progress`, `completed`,
`cancelled`, and `overdue`.

## Frontend

The vanilla HTML/CSS/JavaScript interface is served by FastAPI at `/`,
`/customer-registration`, and `/customer`. Frontend assets are served under
`/static/`. Open the UI through the same origin as FastAPI, for example
`http://127.0.0.1:8000/`; no separate frontend server is required.

The registration, customer intelligence, and analytics pages use same-origin
FastAPI APIs. The UI includes registration, search, pagination,
customer/company/contact/enquiry details, activity creation, follow-up
completion, a filterable activity timeline, and the AI assistant's explicit
action confirmation flow. The analytics dashboard presents structured
database-calculated customer, enquiry, pipeline, activity, and follow-up
metrics. Authentication remains deferred.

## Sales analytics

The analytics endpoints use SQLAlchemy aggregates over the CRM tables and do
not ask the LLM to calculate metrics:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/analytics/overview` | Customer, enquiry, monthly activity, and follow-up KPIs |
| `GET` | `/api/analytics/customers` | Status/stage/industry distributions and customer creation trend |
| `GET` | `/api/analytics/enquiries` | Status/priority/product distributions, value, and enquiry trend |
| `GET` | `/api/analytics/activities` | Meeting/call/follow-up trend and status distributions |
| `GET` | `/api/analytics/pipeline` | Enquiry count and estimated value grouped by the customer's recorded sales stage |
| `GET` | `/api/analytics/followups` | Pending/completed/overdue/cancelled counts and due windows |

All endpoints accept optional inclusive `start_date` and `end_date` query
parameters. The customer, enquiry, and activity endpoints also accept
`grain=daily`, `weekly`, or `monthly` (default `monthly`). Follow-up due
windows use the application server's UTC date. Active customer totals are
current status counts; date filters apply to record creation or activity dates,
not to historical status snapshots.

Enquiry-to-meeting conversion is based on distinct enquiries with a linked
meeting. The schema does not retain a proposal outcome per meeting, so
meeting-to-proposal conversion is returned unavailable. The won/lost rate uses
the existing customer sales stages and is unavailable if there are no won/lost
customers in the selected period.

## CRM assistant

`POST /api/agent/chat` accepts a message and an optional customer ID. The
assistant uses controlled CRM service-backed tools to find customers and read
customer profiles, enquiries, meetings, calls, follow-ups, and activity.
Responses include tool names, safe input summaries, and success status. Read
operations use controlled CRM services; the model does not execute SQL.

Set `GEMINI_API_KEY` and optionally `GEMINI_MODEL` to enable requests. The
application starts without an API key; calling the chat endpoint without one
returns a safe `503 llm_not_configured` API error. Agent tests use deterministic
mock chat models and an isolated in-memory SQLite database; they do not make
paid provider requests. LangChain's current `create_agent` API is used with
Google's official Gemini integration.

### Confirmed CRM actions

The assistant may propose one of these actions after retrieving the relevant
CRM records: create a meeting, create a follow-up, record the result of an
already completed call, or mark a follow-up complete. Proposals do not write
to the database. The customer page presents the pending action with explicit
Confirm and Cancel choices; confirmation performs the write through the
existing CRM services, while cancellation makes no CRM changes. Recording a
call result never places a call.

The API endpoints are `POST /api/agent/actions/{action_id}/confirm` and
`POST /api/agent/actions/{action_id}/cancel`. Pending proposals expire after
`AGENT_ACTION_TTL_SECONDS` (default `300`, configurable from `30` to `3600`
seconds). Pending action state and LangGraph checkpoints are held in process
memory, so proposals do not survive an application restart and are not shared
between separate application workers.
