# Local degree planning API

The FastAPI app exposes the reviewed Computer Science, B.S. degree plan for
2026–2027. It reads catalog evidence from Supabase on demand. Submitted coursework
stays in request memory. Successful evaluation responses carry `Cache-Control: no-store`.

## Run locally

From the repository root in PowerShell, using the project's Python 3.12 virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload
```

With the environment activated, `uvicorn backend.main:app --reload` is equivalent.
The default listener is `http://127.0.0.1:8000`. Interactive API documentation is
available at `/docs` and the OpenAPI contract at `/openapi.json`.

For catalog enrichment, set the HTTPS `SUPABASE_URL` and either
`SUPABASE_SECRET_KEY` or its fallback `SUPABASE_SERVICE_ROLE_KEY` in the server
environment or the repository-root `.env`. Process values take precedence for
each variable. Keep both privileged keys out of browser builds. Listing programs
and opening the API documentation work without credentials. An evaluation lazily
acquires one client for all three readers and releases its HTTP sessions at
request completion.

## Endpoints

**GET `/api/v1/planning/programs`** returns an array of supported combinations.
Use the keys and years from this list when constructing requests.

```json
[{"program_key":"computer-science-bs","program_title":"Computer Science, B.S.","catalog_year":"2026-2027","required_credits":120}]
```

**POST `/api/v1/planning/evaluate`** accepts JSON with the three required fields
`program_key`, `catalog_year`, and `completed_courses`. An empty coursework list
is valid. For example:

```json
{
  "program_key": "computer-science-bs",
  "catalog_year": "2026-2027",
  "completed_courses": [
    "CS 110",
    {"course_code": "CS 120", "grade": "B"},
    {"course_code": "MTH 229", "grade": null}
  ],
  "reported_credits": {},
  "critical_thinking_candidates": true
}
```

The optional JSON boolean `critical_thinking_candidates` selects the response.
Omitting it or sending `false` returns the existing `2.1.0` handoff. Sending
`true`, as above, returns `2.2.0` with Core I Critical Thinking candidates.
Strings, numbers, and null for this field return HTTP 422.

Coursework records accept an optional string or null `grade` and an optional
boolean `equivalency_verified`. Submit each reported attempt. The service
normalizes codes and retains conflicting or missing grade evidence.
`reported_credits` is optional. Its keys must identify entered courses, and its
values must represent finite positive credit amounts. Numeric strings are also
accepted. The service checks the catalog range when it retrieves course evidence.

| HTTP status | Consumer behavior |
| --- | --- |
| 200 | Consume the handoff directly. Check enrichment availability, coverage, and per-course eligibility, including UNKNOWN. A valid audit can contain unavailable optional evidence. |
| 404 | Refresh the supported program/year selection. |
| 422 | Correct malformed coursework, unsupported fields, or invalid reported credits. Validation responses identify affected top-level fields when available. |
| 500 | The server could not produce a valid handoff. The response contains a generic error. |

Both handoff versions use the same **v1** route. In a v2.2 response, find the
additional CT slot by `requirement_id == "t0_row_0005"` in
`flexible_candidates.requirements`. Read its `generation_status`, `reasons`,
and `withheld_courses` when its candidate list is empty or partial. Each candidate's
`course_eligibility_reference` joins to `course_eligibility.courses[].course_code`,
including when the course also appears under another requirement. MTH 229 can
meet the explicit Core I and major calculus rules while the additional CT slot
remains open.

The React client can display requirement
progress, warnings, and per-course evidence from this object. The Schedule
Generator can join flexible memberships to requirements by ID and resolve
`course_eligibility_reference` by course code. It still checks term offerings and
section conflicts. The canonical field and status definitions are in the
[planning handoff contract](planning_handoff.md).

## Authentication and deployment

This stage exposes an unauthenticated local development endpoint. Keep the
listener on loopback. Browser access is configured for the Vite origins
`http://localhost:5173` and `http://127.0.0.1:5173`. A caller outside a browser
can invoke the endpoint independently of CORS.

The React app initializes Supabase with its publishable key, signs in with
`signInWithPassword`, and tracks the session with `getSession` and
`onAuthStateChange`. Before shared deployment, decide who may request plans and
how the scheduler authenticates. For authenticated browser requests, send the
session access token in the Authorization bearer header. The backend must verify
the token against the project's signing configuration and expected claims before
allowing privileged reads. Configure the deployed frontend origin and authorization
header, HTTPS, request limits, and logging that excludes coursework, tokens, and
server keys. [Supabase JWT verification](https://supabase.com/docs/guides/auth/jwts)

## Automated verification

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -p "test_*.py"
```

HTTP tests override the request dependency with a SELECT-only fixture and compare
the complete response to a direct planning call.

## Saved academic information

Apply [migration 003](../backend/migrations/003_student_academic_records.sql) in a
development Supabase project. Server configuration also needs
`SUPABASE_PUBLISHABLE_KEY` (or the legacy `SUPABASE_ANON_KEY`) from the same project.
FastAPI verifies the Supabase access token, then calls the academic record RPCs
with that token so PostgreSQL can identify the owner. The existing privileged
Supabase client remains responsible for catalog evidence reads during evaluation.

All requests below require the student's Supabase bearer token:

- `GET /api/v1/student/academics` returns the saved program, catalog year,
  revision and ordered coursework attempts. It returns JSON null for a new account.
- `PUT /api/v1/student/academics` saves a complete academic record. Include
  `expected_revision` from the previous read, or null for the first save.
  Each attempt has a stable UUID and can include a grade, reported credits,
  and an equivalency flag. A stale revision returns HTTP 409.
- `POST /api/v1/student/academics/evaluate` takes the saved revision and
  evaluates the corresponding record. The audit is regenerated from saved
  coursework and current catalog evidence.

The reporting scope remains the CS B.S. program for 2026–2027. Developer setup
still requires both FastAPI and the Vite app.

## Saved semester plans

Apply [migration 004](../backend/migrations/004_student_semester_plans.sql)
after migration 003. Planner uses the same Supabase bearer token and server
configuration as saved coursework.

- `GET /api/v1/student/planner` returns the signed-in student's saved semesters
  and generator settings.
- `PUT /api/v1/student/planner/semesters` saves a complete semester and its
  ordered course snapshots. Send a client-generated UUID and null
  `expected_revision` on creation. Retrying that creation with the same
  ID and payload returns the existing revision. Edits send the last revision.
- `DELETE /api/v1/student/planner/semesters/{plan_id}` takes
  `expected_revision` in the request body.
- `PUT /api/v1/student/planner/preferences` saves the selected term and target
  credits. Generating a schedule also saves those settings.

Stale edits or deletes return HTTP 409. A generated schedule is saved when the
student chooses **Use this schedule**. A manually edited semester is saved when
the student clicks **Save Semester**. Course and section labels are snapshots
so older plans remain readable when catalog data changes.
