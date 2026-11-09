> Current source: courses.csv, verified with the reviewed snapshot manifest. See [November revalidation](data/prerequisites/investigation/NOVEMBER_SNAPSHOT_REVALIDATION.md). Earlier UNVERIFIED/provenance-blocker statements below describe the earlier review and are superseded. Database approval remains separate.

# Prerequisite and corequisite pipeline

The snapshot manifest is approved for source provenance. Migration 001 is
already applied according to the user; do not reapply it. The optional Supabase
Python-client interface is a separate, unapplied migration 002. See
[API validation, RPC deployment and rollback](PREREQUISITE_SUPABASE.md).
All database modes require reviewed metadata/manifest inputs, and both apply
modes require input and manifest hash acknowledgments. Earlier deployment
reports retain historical findings and are not current deployment approval.

This pipeline prepares Marshall 2026–2027 catalog rules. It does not establish
student eligibility. No production migration or database operation was performed
during implementation. Commands below are run from the repository root in PowerShell.

## Files and architecture

- `parse_prerequisites.py`: existing lexer, recursive Boolean parser, and bounded
  DNF conversion extended to distinct prerequisite/corequisite fields, source
  normalization, external conditions, catalog identity checks and metadata audits.
- `seed_prerequisites.py`: offline input validation, separate read-only database
  planning and explicit transactional apply. No import-time I/O or dotenv loading.
- `prerequisite_supabase.py`: optional Supabase snapshot/import RPC transport;
  see `PREREQUISITE_SUPABASE.md` for separate deployment and approval commands.
- `validate_prerequisites.py`: reproducible full-catalog and CS degree reports.
- `../migrations/001_course_rules_catalog_year.sql`: manually reviewed migration.
- `../tests/test_prerequisite_pipeline.py`: standard-library parser and SQLite tests.
- `../tests/run_prerequisite_postgres_tests.py`: isolated real PostgreSQL tests.
- `data/prerequisites/parsed_all_v2.json`: full version-2 parse; original baseline
  `parsed_all.json`, `parsed_cs.json`, and `parsed_mth.json` are preserved.
- `data/prerequisites/validation_report.json` and `.md`: full diagnostic details
  and readable report, including source checksums and degree course coverage.
- `data/prerequisites/seed_validation.json`: offline seeder validation and input hash.
- `../../requirements-prerequisites.txt`: optional PostgreSQL driver and Supabase SDK. Offline work
  has no dependencies beyond Python 3.12.

The checkout has Pydantic course models and Supabase REST seed scripts, but no
SQLAlchemy models, checked-in schema initialization, or migration runner. This
change supplies a numbered SQL migration without importing the existing seed
scripts (some of which perform live I/O at import time). Direct PostgreSQL via
psycopg remains available: independent REST upserts cannot atomically replace all
three rule tables. The optional API mode uses one transactional RPC instead.
Supabase provides PostgreSQL connections; use an authorized
direct or session-pooler connection for a later database run.

`courses.csv` is ignored and absent in this worktree. The checked-in
`courses.csv` contains the full 6,692-course catalog and is explicitly
selected below. No scrape, attribute CSV rewrite, or backup regeneration is needed.

## Meaning and conservative decisions

Different `ruleclause` rows mean **OR**. Courses within each clause mean **AND**.
The parser keeps nested ASTs and produces DNF only when the entire expression is
course-only, within the 256-clause bound, and has compatible duplicate atoms.
AND binds more tightly than OR, matching the existing parser convention. Explicit
parentheses override it; prose whose academic scope is unclear remains for review.

Version 2 keeps the old top-level expression fields for diagnostics. The
`rules` list is the seeding contract. In particular, old top-level `dnf_clauses`
may describe prerequisite syntax even when **the complete enrollment rule is
unresolved**. Consumers must never seed or evaluate that legacy field directly.

Each rule exposes `rule_type`, exact `raw_text`, `source_url`, `expression`,
`normalized_text`, `normalizations`, `syntax_parsed`,
`course_expression_representable`, `reference_candidates`,
`missing_course_references`, `review_flags`, `machine_checkable` and
`dnf_clauses`. Course entries also retain every original CSV metadata row,
including catalog level, year, source and observation timestamp.

Course identities retain arbitrary letter suffixes (including `BSC 120LH`) and
hyphenated numbers. Whitespace around a hyphen is normalized only if the exact
hyphenated result exists in the supplied catalog. Actual `NUR 741 -B` remains
unresolved: `NUR 741-B` is absent. `NUR 741B` is not substituted.

Normalization repairs one unmatched terminal closing parenthesis only when the
prefix is balanced and parses, or closes one unmatched leading outer wrapper
when it remains open for the entire expression and the candidate parses.
Internal unmatched groups are not repaired. All repairs retain original text
and record before/after values. Nine primary fields and one CR/PR field qualify;
permission prose remains unsupported even after its punctuation is repaired.

Corequisite-only courses are valid inputs. Explicit AND/OR corequisite syntax
is parsed separately and never converted to prior-completion prerequisites.
The catalog fields alone do not establish whether prior completion satisfies
a corequisite or whether simultaneous enrollment is mandatory. Therefore **all
181 courses with corequisite metadata retain
`corequisite_timing_policy_unresolved`**. Their ASTs are preserved, but no
evaluable relational clauses are created. Ninety corequisite fields additionally
use comma lists; commas are not silently changed into AND or OR. A documented,
authoritative timing/list policy is needed before those rules can be enabled.

`Concurrent PR` is corroborating metadata only. Its course-only Boolean
annotation view must match the main expression's inline concurrency view;
grades and the main prerequisite Boolean tree are left unchanged. Projection
omits other conditions only for this audit comparison, never for evaluating or
seeding the rule. AND and OR lists are supported, with Boolean absorption of
redundant DNF clauses. Two mismatches remain: `PHL 250` and `RST 250` list
`ENG 101` as concurrent in metadata, but the main expression does not annotate it.

`CR/PR` is not interpreted as prerequisite or concurrency policy. Both known
records remain unresolved: `CS 120` metadata omits `CS 110H`; `STA 564` metadata
adds `STA 546`. Its stray closing parenthesis can be normalized without resolving
the disagreement. Primary/metadata code sets and ASTs remain available for review.

ACT/SAT labels and literal scores, placement exams, admission markers,
proficiency/workshop markers, narrowly recognized permission conditions and
academic standing are structured `EXTERNAL` leaves. Exact atom wording remains in `raw_text`.
The published phrase “with a score of” is stored as `catalog_score`; it is not
automatically turned into a universal numeric >= policy. Admission scores such
as 1, 4 or 5 may be administrative codes. `verification` is **UNKNOWN**.
Unrecognized prose remains `UNSUPPORTED`. Mixed course/external AND/OR trees
are retained in full and never reduced to their course-only alternatives.

Machine-checkable here means complete course-rule representation with validated
references, not verifiable from the application's current student data. Minimum
grades require grade evidence. Concurrent enrollment requires schedule evidence.
Reported completed course codes alone do not verify either. Unsupported student
evidence and all unresolved rules must produce **UNKNOWN**, not an eligibility
approval. No metadata is also not an eligibility approval.

## Migration and legacy compatibility

`backend/migrations/001_course_rules_catalog_year.sql` is already applied
according to the project owner. Do not reapply it for the optional API transport.
The following describes that existing schema migration.
It adds a nullable catalog-year FK and provenance URL, review status/flags,
lossless JSON source metadata and a payload fingerprint. It discovers the old
two-column unique constraint by its columns and replaces it with uniqueness on
`(course_id, rule_type, catalog_year_id)`. A partial unique index retains the old
uniqueness for legacy NULL-year rows. New imports always resolve a non-NULL year
by `catalog_label`; no database ID is hardcoded.

Legacy rows retain their data and NULL year, with default review status. Consumers
must exclude NULL-year rows from dated eligibility checks even if their old
`machine_checkable` value is true. Existing FK behavior is retained. The migration
provides PK identity generators only for plain bigint keys without an existing
identity/default, positioning new sequences after existing IDs. Ready dated
rules must have a fingerprint and no review flags. Existing RLS, table grants
and policies are not broadened; an authorized import role needs the existing
table privileges and applicable identity-sequence privileges.

Before applying, back up all three rule tables and inspect the actual deployment
schema, constraint names/columns, PK defaults, grants/RLS and catalog-year labels.
The SQL is a one-time transaction, not a repeatable migration runner. A failure
rolls back its DDL/data changes. As with PostgreSQL sequences generally, sequence
increments used by later failed imports are not rolled back; gaps are harmless.

Rollback before commit is ordinary transaction rollback. After commit, prefer
restoring the reviewed database backup or a forward corrective migration. Do
not drop source JSON or reintroduce two-column uniqueness after importing
multiple years: that would lose review evidence or conflict with valid rules.
Application rollback can leave these additive columns in place, but the old
seeder must not be used because its conflict target lacks catalog year.

## Reproduce parsing, validation and tests (offline)

```powershell
python -m backend.scripts.parse_prerequisites --input backend/scripts/catalog_prerequisites.csv --catalog backend/scripts/courses.csv --output backend/scripts/data/prerequisites/parsed_all_v2.json
python -m backend.scripts.validate_prerequisites --catalog backend/scripts/courses.csv --input backend/scripts/catalog_prerequisites.csv --degree backend/scripts/data/degree_requirements/resolved/computer_science_bs_resolved.json --output backend/scripts/data/prerequisites/validation_report.json
python -m backend.scripts.seed_prerequisites --input backend/scripts/data/prerequisites/parsed_all_v2.json --catalog backend/scripts/courses.csv --dry-run --report backend/scripts/data/prerequisites/seed_validation.json
python -m unittest discover -s backend/tests -v
```

Omitting `--dry-run` still performs offline validation, with no connection.
The seeder reparses preserved source rows and rejects artifacts with edited
readiness flags, ASTs or DNF. `approved_for_seeding` stays false; the explicit
apply gate below is a separate acknowledgment of the exact reviewed artifact.
The JSON parse timestamp changes on regeneration; report counts and input
checksums are reproducible, and a new parse requires a new review hash.

Optional isolated PostgreSQL tests (never use an existing cluster):

```powershell
python -m pip install -r requirements-prerequisites.txt
python -m backend.tests.run_prerequisite_postgres_tests
```

This runner requires `initdb` and `pg_ctl` on PATH, creates its own temporary
data directory, binds only 127.0.0.1 on a fresh port, creates a test database,
applies the migration only there, then stops/removes the cluster. It does not
read environment database URLs or credentials. Its temporary local trust
authentication is confined to the ephemeral loopback server.

## Later database planning and apply (NOT executed on Supabase)

With migration 001 already present, install the optional driver and
provide an authorized PostgreSQL DSN as `PREREQUISITE_DATABASE_DSN` through your
process secret manager. Do not put it on the command line or in this document.
These modes deliberately do not read `.env` or reuse Supabase REST keys.

Read-only database dry run:

```powershell
python -m backend.scripts.seed_prerequisites --input backend/scripts/data/prerequisites/parsed_all_v2.json --catalog backend/scripts/courses.csv --metadata backend/scripts/catalog_prerequisites.csv --snapshot-manifest backend/scripts/data/prerequisites/investigation/snapshot_manifest.json --db-check --report prerequisite-db-plan.json
```

This uses a read-only repeatable-read transaction. It resolves actual target
and referenced course IDs and catalog labels, reads existing rules/children,
and reports insert/update/skip/review counts, missing references, fatal unmapped
targets/years, and existing same-year rules absent from the input that will be
retained. Offline validation cannot know database ID coverage; this explicit
step provides it. Database missing references downgrade the **entire course**
to unresolved parent-only records. Missing/ambiguous targets or catalog years
block the entire apply operation.

After reviewing the exact artifact and database plan, explicit apply:

```powershell
$reviewHash = (Get-FileHash backend/scripts/data/prerequisites/parsed_all_v2.json -Algorithm SHA256).Hash.ToLowerInvariant()
$snapshotHash = (Get-FileHash backend/scripts/data/prerequisites/investigation/snapshot_manifest.json -Algorithm SHA256).Hash.ToLowerInvariant()
python -m backend.scripts.seed_prerequisites --input backend/scripts/data/prerequisites/parsed_all_v2.json --catalog backend/scripts/courses.csv --metadata backend/scripts/catalog_prerequisites.csv --snapshot-manifest backend/scripts/data/prerequisites/investigation/snapshot_manifest.json --apply --approve-reviewed-input $reviewHash --approve-reviewed-snapshot $snapshotHash --report prerequisite-apply-result.json
```

The hash acknowledgment does not turn unresolved records into validated ones.
Every unresolved rule is stored with `machine_checkable=false`, review flags,
raw/source JSON and **no child clauses**. For a combined course with one
unresolved rule, both rules are conservatively blocked. All source information
survives even when a required course cannot be mapped. Missing target courses
cannot be inserted as parents; their evidence remains in the review artifact,
and the batch cannot apply until resolved.

Apply locks the three rule tables to serialize imports, rebuilds the plan
inside one repeatable-read transaction, upserts each exact course/type/year
parent, and replaces only its own children. No other year or unmentioned rule
is deleted. Full payloads and actual children are compared, not fingerprints
alone. Equal rows skip without child churn; drift updates. Verification runs
before commit and failure rolls back the entire import. Locking can briefly
block other rule writers, so schedule the import appropriately.

## Read contract for future components

Join `courserule` to `catalogyear` using the requested `catalog_label`, target
`course`, `ruleclause` and `rulecourse`/required `course`. Read all rule types for
the course together, not only prerequisites. Return:

```text
course_code, catalog_year, source_url,
rules[{rule_type, raw_text, machine_checkable, parse_status, review_flags,
       clauses[[{course_code, minimum_grade, concurrent_allowed}]],
       source_metadata}]
```

Reject undated legacy rows for dated decisions. Any rule with review flags,
non-ready status, `machine_checkable=false`, no clauses, or an empty clause is
**UNKNOWN**. Do not evaluate empty conjunctions as true. `COREQUISITE` always
retains its type; never reinterpret it as prior completion. A future evaluator
must inspect available evidence before evaluating grade or concurrency atoms;
absent evidence is UNKNOWN. Three-state logic should propagate UNKNOWN through
AND/OR and must not turn it into true. The current pipeline intentionally
does not implement a student eligibility engine.

`source_metadata.course.rules[*].expression` exposes external/unsupported
conditions and original Boolean grouping for review/future evaluation. Missing
metadata means “no published prerequisite-related metadata in this catalog
export”, not “unrestricted”. Runtime consumers need the catalog coverage report
to distinguish that state from an unmapped course or unseeded data.

## Review and deployment order

1. Inspect the migration, source checksums, normalizations, missing-code list,
   comma lists, corequisite policy and the two concurrency/CR-PR discrepancies.
2. Run offline tests and disposable PostgreSQL tests. Review CS degree coverage;
   its 47 explicit course codes exclude open elective/attribute expansion.
3. Back up the deployment schema/data and confirm migration 001 is present;
   do not reapply it. Optional API modes need the separate migration 002 and
   deployment procedure in `PREREQUISITE_SUPABASE.md`.
4. Run `--db-check` with an authorized DSN; resolve fatal target/year mappings.
5. Review unresolved records and planned updates; acknowledge the exact input
   hash and run explicit apply only after separate authorization.
6. Rerun `--db-check`: unchanged rules should all skip. Verify dated parent counts,
   DNF joins, grade/concurrency fields, zero unresolved child clauses, source
   JSON, and that other catalog years and legacy rows are retained.
7. Keep planning/scheduling consumers fail-closed until their UNKNOWN handling,
   evidence model, and catalog corequisite policy are separately implemented.

## Implementation validation results

Earlier revision validation (not a deployment approval): the local standard-library suite passed 48 tests with zero remaining failures.
The deployment review revision passes 53 offline tests; its database changes
have not been rerun against PostgreSQL. See the readiness report for blockers.
The disposable PostgreSQL 18 suite passed five tests using psycopg 3.3.8,
including the actual migration against empty and populated legacy schemas.
Its full-catalog transaction inserted 2,043 parents (1,393 validated and 650
unresolved); the repeated import skipped all 2,043. Rollback, read-only
enforcement and concurrent imports were tested on that disposable database.
The sandbox initially prevented initdb from completing; the successful test
run used an authorized local process outside the sandbox. The cluster was
stopped and removed. No Supabase migration, connection or seeding was performed.

The catalog result is 1,393 ready / 542 review versus the 1,615 / 320 baseline.
There are 280 courses referencing 181 missing catalog codes, 181 courses with
unverified corequisite timing, 108 courses with external/unsupported conditions,
90 comma-list syntax failures, two concurrency mismatches, two CR/PR
discrepancies, one duplicated prerequisite field and one unresolved hyphenated
identity. Counts overlap. See the full JSON for per-course details.

CS B.S. coverage is 47 explicit courses: 27 with representable course rules,
11 with unresolved conditions, nine verified catalog courses without published
prerequisite-related metadata and zero unmatched course codes. No category
implies verified eligibility.
