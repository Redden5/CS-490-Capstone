# Optional Supabase Python-client prerequisite transport

Migration **001 is already applied according to the project owner**. Do not
reapply, recreate, or reverse it for this feature. Its file is unchanged
(SHA256 `64eb9be5c406dd9709033eacdc88ea02f7ed810d3608e1b54f65daea2e6d0646`).
The optional **002 migration is not deployed**. No production connection or
data operation was made during implementation. SQL was exercised only in a
disposable local PostgreSQL 17 container against the manual schema reconstruction.

The existing `--dry-run`, `--db-check`, and `--apply` modes retain their behavior.
`--api-check` and `--api-apply` use `supabase.create_client`, with the same
`SUPABASE_SECRET_KEY` then `SUPABASE_SERVICE_ROLE_KEY` precedence as
`seed_course_attributes.py`. Unlike that script, this pipeline never loads
dotenv. Set credentials in the process environment through an approved secret
manager; do not put them in command arguments, logs, reports, or source files.
The API client requires an HTTPS URL and does not create or persist an Auth
user session. Never use a browser/anon/publishable key or sign a student in to
this administrative client.

## Interface and safety boundaries

| Mode | Connection | Data mutation | Preconditions |
| --- | --- | --- | --- |
| default / `--dry-run` | None | None | Valid parser artifact and catalog |
| `--db-check` | Explicit PostgreSQL DSN | None | Migration 001; approved source manifest |
| `--apply` | Explicit PostgreSQL DSN | One transaction | Above plus both approval hashes |
| `--api-check` | Supabase client, snapshot RPC only | None | Migration 002; service role; approved manifest |
| `--api-apply` | Supabase client, snapshot plus import RPC | One import transaction | Above plus both approval hashes |

`public.prerequisite_snapshot_v1()` is a STABLE, SECURITY INVOKER function that
reads all five tables in one SQL snapshot and returns one JSON object. It is
not a set-returning function, so an API row limit cannot silently truncate the
6,692-course catalog. It also avoids mixing separately paginated table reads.
PostgREST runs STABLE RPC calls in read-only transactions, including POST.
The function performs no writes and is tested inside a PostgreSQL READ ONLY
transaction. [PostgREST transaction contract](https://docs.postgrest.org/en/stable/references/transactions.html).

Both functions require `current_user = 'service_role'` **and** the database
role's BYPASSRLS attribute, use `row_security=off`, and use only qualified public
tables with `search_path=pg_catalog`. PUBLIC, anon and authenticated EXECUTE
privileges are revoked in the same transaction as creation. An accidentally
granted EXECUTE privilege alone still cannot pass the role check. These are
invoker functions: there is no definer-privilege escalation. They depend on the
existing table/sequence privileges and do not grant new table privileges,
change RLS, or introduce permissive policies. Administrative SQL owners can
alter functions; no function can constrain a privileged schema administrator.
[Supabase functions](https://supabase.com/docs/guides/database/functions).

The CLI reparses the exact input, verifies catalog and raw-metadata hashes,
checks approved manifest evidence/year/source-row equality, and checks both
approval hashes **before constructing a client**. It uses the same `build_plan`
as PostgreSQL mode: duplicate mappings are rejected, missing targets/years
block writes, missing referenced courses downgrade the entire course, and
unresolved rules preserve raw/source JSON with no clauses. No course identities,
equivalencies, corequisite timing, or academic policy are changed by this mode.

`public.prerequisite_import_v1(jsonb,jsonb,text,text,text,text)` receives the full
expected database snapshot, all incoming rule payloads, artifact/manifest
hashes, and their explicit acknowledgments. It locks the rule tables in the
same order as the direct seeder and takes SHARE locks on course/catalogyear.
It rejects a changed database snapshot before modifying anything. It then
compares actual complete payloads, skips equal rules without changing child
IDs, and replaces only the children belonging to changed course/type/year
keys. Unmentioned rules, other years and legacy undated rows are retained.
It validates structural invariants and checks every stored payload before
returning counts. Any SQL error aborts the entire request transaction, including
earlier inserts/updates/deletes. PostgreSQL identity sequences can still have
gaps after rollback; identities are never reset to hide these normal gaps.

The RPC checks acknowledgment equality and structural safety; it cannot
independently prove that a human reviewed a local file or re-run Python's
academic parser. A service-role holder already has direct table privileges.
Use the validated CLI rather than constructing raw RPC payloads. The hashes
are review acknowledgments, not authentication tokens or digital signatures.

## Local tests (no production connection)

From the repository root:

```powershell
python -m pip install -r requirements-prerequisites.txt
python -m unittest discover -s backend/tests -q
python -m backend.tests.run_supabase_sdk_tests
python -u -m backend.tests.run_docker_staging_tests --include-rpc
```

The SDK tests intercept all HTTP with fake responses using dummy keys. The
Docker runner uses a newly named, loopback-only PostgreSQL 17 container and
removes it and its storage afterward. The staging SQL adapter mimics the RPC
parameter contract; it does not run a hosted Supabase gateway. The schema is a
**manual reconstruction from read-only evidence, not an authoritative pg_dump**.
RPC tests add test-only role-switch memberships to emulate the authenticator;
these memberships are not claimed as production schema evidence and are not
part of migration 002.

## Deployment step 1: read-only checks before any change

Review migration 002 and its rollback file and take an approved schema/data
backup. Confirm that migration 001's columns, unique key and foreign keys are
present and that the reported service-role privileges remain valid. These
operator queries are read-only; they have **not** been run on Supabase:

```sql
BEGIN TRANSACTION READ ONLY;
SELECT catalog_year_id,source_url,parse_status,review_flags,source_metadata,content_sha256
FROM public.courserule LIMIT 0;
SELECT conname,pg_get_constraintdef(oid)
FROM pg_catalog.pg_constraint
WHERE conrelid IN ('public.courserule'::regclass,'public.ruleclause'::regclass,'public.rulecourse'::regclass);
SELECT rolname,rolbypassrls FROM pg_catalog.pg_roles WHERE rolname='service_role';
SELECT t,has_table_privilege('service_role',t,'SELECT') AS can_read
FROM unnest(ARRAY['public.course','public.catalogyear','public.courserule','public.ruleclause','public.rulecourse']) AS t;
SELECT t,p,has_table_privilege('service_role',t,p) AS permitted
FROM (VALUES ('public.courserule','INSERT'),('public.courserule','UPDATE'),
 ('public.ruleclause','INSERT'),('public.ruleclause','DELETE'),
 ('public.rulecourse','INSERT'),('public.rulecourse','DELETE')) AS needs(t,p);
SELECT t,pg_get_serial_sequence(t,c) AS sequence_name,
 has_sequence_privilege('service_role',pg_get_serial_sequence(t,c),'USAGE') AS can_generate_ids
FROM (VALUES ('public.courserule','rule_id'),('public.ruleclause','clause_id'),
 ('public.rulecourse','rule_course_id')) AS ids(t,c);
SELECT to_regprocedure('public.prerequisite_snapshot_v1()') AS existing_snapshot,
 to_regprocedure('public.prerequisite_import_v1(jsonb,jsonb,text,text,text,text)') AS existing_import;
ROLLBACK;
```

Stop if required permissions are false, the schema differs from 001, or RPC
names already exist unexpectedly. Do not automatically grant privileges or
replace an unknown function. Confirm the API exposes `public` and trusted
secret/service credentials map to `service_role` through the existing
authenticator. These gateway settings were not included in the table evidence.

## Deployment step 2: explicit schema modification, separate approval

Exact SQL: `backend/migrations/002_prerequisite_supabase_rpc.sql`. Execute that
file in the approved SQL editor as postgres, or, using an operator-configured
psql service profile (not a credential embedded in this command):

```powershell
# MODIFIES SCHEMA ONLY: creates two RPC functions and their EXECUTE grants.
# capstone_migration is an operator-created connection profile, not supplied here.
psql 'service=capstone_migration' -X -v ON_ERROR_STOP=1 -f backend/migrations/002_prerequisite_supabase_rpc.sql
```

It uses ordinary CREATE FUNCTION so an unexpected existing definition blocks
deployment; it does not silently replace one. It includes a PostgREST schema
cache reload notification. It does not modify migration 001, tables, identities,
constraints, RLS policies, table grants, or academic data.

After installation, check access with this read-only query (service_role true,
anon/authenticated false, `prosecdef=false`, snapshot volatility `s`, import `v`):

```sql
SELECT p.oid::regprocedure,p.prosecdef,p.provolatile,p.proconfig,
 has_function_privilege('service_role',p.oid,'EXECUTE') AS service_exec,
 has_function_privilege('anon',p.oid,'EXECUTE') AS anon_exec,
 has_function_privilege('authenticated',p.oid,'EXECUTE') AS authenticated_exec
FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='public' AND p.proname IN ('prerequisite_snapshot_v1','prerequisite_import_v1');
```

## Deployment step 3: read-only API validation

Make `SUPABASE_URL` and either `SUPABASE_SECRET_KEY` or
`SUPABASE_SERVICE_ROLE_KEY` available in the process environment through your
approved credential mechanism. No `.env` file is loaded. Configure only the
intended target project. These commands do not set or print credentials:

```powershell
$prerequisiteArgs = @(
  '--input', 'backend/scripts/data/prerequisites/parsed_all_v2.json',
  '--catalog', 'backend/scripts/courses.csv',
  '--metadata', 'backend/scripts/catalog_prerequisites.csv',
  '--snapshot-manifest', 'backend/scripts/data/prerequisites/investigation/snapshot_manifest.json'
)
# OFFLINE: no client or database connection.
python -m backend.scripts.seed_prerequisites @prerequisiteArgs --dry-run --report prerequisite-offline.json
# READ ONLY: snapshot RPC, no import RPC and no REST table mutations.
python -m backend.scripts.seed_prerequisites @prerequisiteArgs --api-check --report prerequisite-api-plan.json
```

Review missing references, fatal mapping errors, rule changes, retained
unmentioned rules, and review counts. A successful plan is not permission to
write. Reconcile any unexpected differences, especially CS B.S. references.
There is no fallback to potentially RLS-filtered REST pagination if the RPC is
absent. Use existing `--db-check` with an authorized DSN if 002 is not installed.

## Deployment step 4: explicitly approved data modification

Only after separate approval of the exact source files and database plan:

```powershell
# DATA WRITE: one atomic import RPC, preceded by a fresh snapshot read.
# Calculate these only after review; calculating a hash does not itself approve data.
$reviewHash = (Get-FileHash backend/scripts/data/prerequisites/parsed_all_v2.json -Algorithm SHA256).Hash.ToLowerInvariant()
$snapshotHash = (Get-FileHash backend/scripts/data/prerequisites/investigation/snapshot_manifest.json -Algorithm SHA256).Hash.ToLowerInvariant()
python -m backend.scripts.seed_prerequisites @prerequisiteArgs --api-apply --approve-reviewed-input $reviewHash --approve-reviewed-snapshot $snapshotHash --report prerequisite-api-apply.json
# READ ONLY: expect unchanged incoming rules to skip.
python -m backend.scripts.seed_prerequisites @prerequisiteArgs --api-check --report prerequisite-api-after.json
```

The initial complete local fixture imports 2,043 parents, 2,655 clauses and
3,535 course atoms, with 650 unresolved parents and no unresolved children.
Do not hardcode these counts as production truth if catalog mappings or other
rules differ. The post-write report and repeated read-only plan must agree.

## Rollback, timeouts and remaining deployment gates

To remove the RPC interface after separate approval, execute the exact
`backend/migrations/002_prerequisite_supabase_rpc_rollback.sql`, or:

```powershell
# SCHEMA MODIFICATION: drops ONLY the two RPC functions, without CASCADE.
psql 'service=capstone_migration' -X -v ON_ERROR_STOP=1 -f backend/migrations/002_prerequisite_supabase_rpc_rollback.sql
```

This rollback leaves migration 001 and **all imported data** intact. It is not
an undo-import command. Restore a reviewed backup or develop a separately
reviewed scoped data reversal if a committed import must be reversed. Direct
PostgreSQL modes remain available after removing 002.

Both functions declare a 120-second statement timeout; import also declares a
10-second lock timeout. The client timeout is 130 seconds. Confirm the hosted
PostgREST version honors function settings and its gateway limits allow the
request. [Supabase timeout configuration](https://supabase.com/docs/guides/database/postgres/timeouts).
For raw SQL callers, set a transaction-local statement timeout before the
outer SELECT; setting one from inside a function is not a replacement for the
caller's statement timer. SQL errors roll back; a lost HTTP response or client
timeout can leave the commit outcome **unknown**. Do not claim rollback from a
transport exception. Run `--api-check` before deciding whether to retry; there
is no automatic partial-batch retry or chunked import.

Approximate compact JSON body sizes with the current complete local fixture
are 8.0 MB for the first import and 15.7 MB for an unchanged repeat, because
the request includes both the complete expected state and the incoming rules.
The service's request-size/time limits and real Auth/gateway mapping remain
**unverified deployment gates**. The local tests validate SQL and SDK contracts,
not the hosted gateway. If those limits are inadequate, use the preserved direct
PostgreSQL transaction mode; do not split this import into independent requests.
Limits may also change with future catalog years or unrelated existing rules.

Manual reconstruction is not pg_dump equivalence; unreported dependencies,
role memberships and environment drift remain possible. The tested server is
PostgreSQL 17.11, while supplied production evidence described 17.6. Review
those evidence limits before deployment. Historical course references, corequisite
timing, grades without student evidence, permissions/admissions and other
unsupported conditions remain UNKNOWN; no successful import establishes eligibility.
