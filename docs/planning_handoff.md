# Degree planning handoff (v1 through v2.2)

The handoff is unofficial planning evidence, not an approved degree audit,
verified transcript, graduation determination, or registration clearance.

## Execution and compatibility

`backend/scripts/preview_degree_audit.py` reads input files, calls
`backend.services.degree_planning.run_degree_plan()`, and writes the requested
reports. Python callers can pass the contents of the resolved degree JSON directly.

```python
from backend.services.degree_planning import run_degree_plan

result = run_degree_plan(
    draft, [{"course_code": "CS 110", "grade": "A"}],
    core_ii_candidates=True, client=supabase_client,
)
preview = result.preview
handoff = result.handoff
```

The two positional inputs are the resolved draft and a list of course codes or
coursework records. Optional keyword inputs include `reported_credits` (course
codes mapped to credit amounts, or pairs), the existing `core_ii_listing`, and
the mode flags `supabase_attributes`, `evaluate_prerequisites`, `core_ii_candidates`,
and `critical_thinking_candidates`. All flags default to false. The default result includes
both the preview and the v1 handoff. With `include_handoff=False`, a legacy
preview-only call returns `None` for the handoff. Prerequisite or flexible enrichment always
builds the handoff. An injected `client` serves every requested Supabase lookup
and bypasses environment and SDK setup. Without an injected client, the first
lookup that needs the database creates one client for that evaluation. The
completed-course, Core II and prerequisite readers share it. A setup failure
is remembered for that evaluation only. A later evaluation can try again.

The audit and handoff calculations live in `backend/services/degree_audit.py`.
The service builds the handoff with `build_planning_handoff()`, whose
`finalize_planning_handoff_v1()` creates explicit candidate placeholders. Optional
prerequisite enrichment happens **after** that finalizer returns.

With prerequisite and flexible candidate modes off, legacy code-list inputs produce the same
preview and `1.0.0` handoff. No prerequisite source files, environment loading,
client creation or queries occur. The existing independent `--supabase-attributes`
option retains its behavior. Neither option is implied by the other.

`--core-ii-candidates` is a separate opt-in mode that implies both completed-course
attribute retrieval and prerequisite evaluation, and produces `2.1.0`. It does not
require specifying the two older flags. Adding them explicitly does not repeat
the lookups or prerequisite batch. The existing v1/v2 modes are unchanged.

With `--evaluate-prerequisites`, the CLI:

1. Validates coursework and the draft's catalog-year label.
2. Computes the same degree-progress results and explicit candidate pool.
3. Reads `backend/scripts/courses.csv` and `backend/scripts/catalog_prerequisites.csv`.
4. Obtains the evaluation's client after validating inputs and reading the reviewed
   sources. An empty prerequisite pool skips both the source reads and client setup.
5. Calls `backend/services/planning_eligibility.py` once for the whole candidate batch.
6. Attaches results to a `2.0.0` handoff and, if requested, the preview JSON.

The opt-in flag works without either output path; the console reports counts and
availability. `--output` includes `course_eligibility` and additional warnings.
`--planning-output` includes the full v2 projection. Existing saved examples are
not upgraded automatically. The frontend and Schedule Generator are unchanged.

### Server-side Supabase configuration

Backend callers can use `backend.services.supabase_service.create_supabase_client()`
and pass its return value as `client` to `run_degree_plan()`. The factory reads
the project-root `.env` with `override=False`, preserving process environment
values for each variable. It then selects `SUPABASE_SECRET_KEY` when nonempty,
falling back to `SUPABASE_SERVICE_ROLE_KEY`. Both are server-side credentials.
The prerequisite import CLI uses the same factory with `load_env_file=False`,
preserving its process-environment-only behavior.

Previously the completed-course attribute reader accepted only `SUPABASE_SECRET_KEY`
and delegated URL validation to the SDK. It now accepts the service-role fallback
and shares the prerequisite reader's HTTPS requirement for `SUPABASE_URL`.
URLs with embedded credentials, queries, fragments or invalid ports are rejected.
Both readers use the existing prerequisite client options with session persistence
and token refresh disabled and a 130-second PostgREST timeout.

Attribute-only CLI runs report a sanitized error on setup failure. Core II runs
retain the audit and mark affected evidence unavailable. A failed prerequisite
lookup preserves affected candidates with UNKNOWN eligibility. The planning
readers use SELECT operations only.

## Coursework input

`--completed "CS 110,CS 120"` and JSON code lists remain supported.
`--completed-file` also accepts mixed records or the existing wrapper:

```json
{
  "completed_courses": [
    "CS 110",
    {"course_code": "CS 120", "grade": "A"},
    {"course_code": "MTH 220", "grade": null}
  ]
}
```

Record fields are `course_code`, optional `grade`, and optional boolean
`equivalency_verified`. False marks an unresolved equivalency; the default does
not independently certify transfer credit. Unknown fields, non-string grades
(except null), invalid identities and malformed JSON are explicit input errors.

The audit receives distinct codes. The evaluator receives the richer evidence,
including repeats. Codes and grade strings are case/spacing normalized; no
highest-grade, latest-attempt or replacement policy is applied. Identical records
collapse inside the existing evaluator; conflicting records remain uncertain.
Combining `--completed "CS 110"` with a graded CS 110 record creates both a missing
grade and a known grade for that course, which is conflicting evidence.

Only A/B/C/D/F comparisons are currently supported. A missing grade is insufficient
when a minimum is required. Plus/minus, pass/fail and transfer marks are not
assigned invented equivalents. F is not successful prerequisite completion.

**Students enter only successfully completed courses.** Optional grades affect
minimum-grade prerequisite evidence only; passing a course need not meet another
course's minimum grade. The degree audit continues to use entered course codes
and excludes those codes from remaining candidates. No failed-course or retake
logic is added. `grades_used_for_degree_progress` remains false. The older v2
warning text is retained for output compatibility; v2.1 describes the passed-only
input policy explicitly.

Normalization preserves existing audit identities while additionally accepting
parser-supported multi-character suffixes and hyphens, such as `BSC 120LH` and
`MUS 123-1`. Suffixes are never stripped. Legacy one-digit codes such as `CS 1`
remain accepted in the disabled audit path, but the prerequisite evaluator's
catalog grammar does not support them: opt-in requests fail validation explicitly
rather than omit evidence or invent a mapping.

## Candidate coverage

The current CLI evaluates the finalizer's sorted union of:

- Missing explicit required courses.
- Missing members of unfinished options in incomplete choice groups.

Already entered courses are excluded. Choice groups, ALL_OF option membership,
review flags, credit progress, requirement summaries and degree dispositions do
not change because of prerequisite results. No course is removed for being UNMET
or UNKNOWN. No course is added from prerequisite diagnostics.

Attribute-only requirements, including Core II categories, remain intact. Their
candidate options are **not generated in v1/v2.0 mode**. Existing
`candidate_option_coverage` includes unfinished credit requirements that lack an attribute mapping.
`NO_CANDIDATES` means no explicit candidate codes, not that every flexible
requirement is complete or that the degree has no remaining work.

### CS Science with Lab progress across handoff versions

For the 2026–2027 CS B.S., `t0_row_0058` retains all five listed lecture/lab bundles. Read `choice_progress.maximum_nonoverlapping_choices_entered` and `choices_remaining_candidate` for progress toward three. With both Physics I lectures and PHY 202 entered, two bundles match and only one choice counts. Three distinct pairs with at least 12 known candidate credits give `CANDIDATE_COMPLETE`. If credit evidence is missing, the count still closes the list of major science recommendations. The source rule and its “or above” alternatives retain their review flags.

In `course_options`, `missing_codes_to_complete_option` describes the original bundle. An optional `candidate_exclusion_reason` explains why that bundle contributes no courses to the eligibility pool. Reasons are `LISTED_CHOICE_COUNT_MET`, `LAB_ALREADY_USED_IN_MAJOR_CHOICE`, and `PHYSICS_I_LECTURE_ALREADY_ENTERED`. For example, after PHY 211 alone, PHY 202 remains a candidate through the PHY 211 option. After PHY 202 alone, either lecture remains an alternative. Other requirement memberships and prerequisite results apply independently.

For `t0_row_0011`, the planner checks a single completed option against year-scoped Core II Natural Sciences memberships for **every** member and at least four known credits. It considers the documented integrated lab PS 111 first, followed by the resolved major pairs in draft order. The qualifying option appears in `attribute_filter.candidate_course_codes_entered` when the disposition is `CANDIDATE_COMPLETE`. The same pair can contribute to provisional major progress, while distinct credit totals count each course once. PS 111 contributes only to Core II. A four-credit lecture alone, unrelated classified courses, and unverified substitutions leave the slot under review. The preview retains raw classification matches and their catalog sources.

Core II science progress requires the entered-course attribute lookup, available through the API, `--supabase-attributes`, or `--core-ii-candidates`. Broader Core II lab options remain unenumerated. Source review and transcript verification still apply. [CS degree requirements](https://catalog.marshall.edu/undergraduate/programs-az/engineering-computer-sciences/computer-science/computer-science-bs/) · [PS 111 catalog entry](https://catalog.marshall.edu/undergraduate/courses-az/ps/)

## V2 eligibility contract

`course_eligibility` contains:

| Field | Meaning |
|---|---|
| `evaluation_status` | Operational outcome below; distinct from rule satisfaction. |
| `default_status` | Always `UNKNOWN`. |
| `catalog_year` | The degree draft's year; never replaced with the current year. |
| `basis` | `STUDENT_REPORTED_EVIDENCE`. |
| `registration_eligibility_determined` | Always false. |
| `source_verification`, `retrieval_status` | Loader evidence, or unavailable/not-requested state. |
| `issues` | Sanitized batch availability diagnostics (`code`, optional `table`). |
| `diagnostic_scope` | `ALL_ALTERNATIVES_NOT_MANDATORY_COURSEWORK`. |
| `grades_used_for_degree_progress` | Always false. |
| `candidate_scope` | CLI projection: `EXPLICIT_DEGREE_REQUIREMENT_COURSES`. |
| `summary` | Counts keyed by `MET`, `UNMET`, `UNKNOWN`. |
| `courses` | Sorted per-course results, including blocked and uncertain candidates. |

Operational statuses:

- `NOT_EVALUATED`: v1 only; evaluation was not requested.
- `COMPLETED`: all requested candidates have complete rule data and were evaluated.
  An unresolved rule, missing grade or timing uncertainty can still produce UNKNOWN.
- `PARTIAL`: the loader returned, but one or more candidate envelopes are incomplete.
  Independently complete candidates retain their own evaluation results.
- `UNAVAILABLE`: expected source/client availability or batch loader failure.
  Every affected candidate is retained as UNKNOWN; no partial read is trusted.
- `NO_CANDIDATES`: empty request; no source reads or client creation are needed.

Each course retains the evaluator's `course_code`, `catalog_year`, `status`,
`basis`, `registration_eligibility_determined`, `reasons`, `rules`, `rule_groups`,
`missing_courses`, `insufficient_grade_evidence`, and optional `source_url`.
`reason` supplies a concise summary code. Status means:

- `MET`: supplied rules are met by reported evidence. It is **not eligibility**.
- `UNMET`: at least one required condition is proven unsatisfied under that evidence.
- `UNKNOWN`: the available evidence cannot establish satisfaction or failure.

`rule_groups` preserves prerequisite and corequisite results separately. A group's
null status means no rule of that type was supplied, not MET. Individual rules and
their clauses preserve AND/OR results. A proven failure can coexist with unresolved
requirements; consumers must retain the explanations as well as aggregate status.

Each course's `loading` object independently includes:

- `data_complete`, `load_status`, `coverage_status`, `evaluability`, and `issues`.
- `excluded_legacy_rule_ids` and `clause_provenance`.
- `catalog_year_id` when a loader response is available.
- `rule_provenance`: normalized parent metadata excluding clauses, including IDs,
  raw text, source URLs, readiness/review flags, source metadata and fingerprints.

Evaluator results can return early for incomplete envelopes. The separate loader
diagnostics and parent provenance must therefore be retained. Degree requirement
`rule_id` and database prerequisite `rule_id` are different namespaces; they are
not join keys to each other.

For each requirement, `prerequisites_evaluated` is true only when its nonempty set
of explicit remaining candidates all has complete rule data and was evaluated.
It does not mean MET. New `prerequisite_evaluation_status` is `COMPLETED`,
`INCOMPLETE`, `UNAVAILABLE`, `CANDIDATES_NOT_ENUMERATED`, or `NOT_APPLICABLE`.
Completed requirements with no remaining candidates retain false. Unenumerated
attribute categories retain false. Existing requirement progress fields and
`offered_sections_evaluated` are unchanged.

## Completeness, failures and timing

The adapter never forces `data_complete`. The unchanged loader verifies pinned
source bytes, stable paginated reads, identities, relationships, fingerprints and
the expected source graph. Its reviewed source evidence currently covers only
2026–2027. Other years do not inherit that evidence.

Completeness is relative to that reviewed snapshot, not proof of unrestricted
database permissions or a transactionally current snapshot. Completely retrieved
unresolved rules remain unevaluable. Missing metadata, missing references, hidden
expected rows, unsupported conditions, mismatched years and absent source evidence
remain UNKNOWN. Even source-confirmed absence of published metadata does not prove
that a course has no prerequisites. Undated rules are not substituted for dated ones.

The CLI uses `concurrent_courses=None`: no proposed same-term schedule has been
reported. Candidate pools are not concurrency evidence. The reusable service accepts
explicit `concurrent_courses`; an empty list states no concurrent courses, while a
nonempty list asserts proposed same-term enrollment. Neither concurrency permission
nor proposed enrollment invents a grade or resolves an unreviewed corequisite policy.

`missing_courses` is a union of diagnostics from **all alternatives**. A MET course
can have a nonempty list for unused OR branches. It can also reflect unavailable
concurrent evidence rather than an absent completion. Do not turn this list into
mandatory degree requirements, prerequisite chains, or automatic schedule additions.
Alternative selection requires the individual rule/clause results and future policy.

Expected configuration, dependency, source-file and loader failures produce
sanitized structured diagnostics and console warnings. A valid degree audit is
still written when possible, and the CLI returns 0 for this degraded success.
Malformed student/draft input and output-file failures return 1. Unexpected adapter
or projection programming errors propagate; they are not relabeled as availability.
Consumers must inspect `evaluation_status`, not just the process exit code.

## Opt-in Core II integration: v2.1.0

Example invocation (performs read-only live catalog/prerequisite queries):

```text
.venv\Scripts\python.exe -m backend.scripts.preview_degree_audit --input backend/scripts/data/degree_requirements/resolved/computer_science_bs_resolved.json --completed-file completed.json --core-ii-candidates --output preview.json --planning-output planning.json
```

Flexible catalog enumeration covers Humanities, Social Sciences (the draft's
"Social Studies"), Fine Arts, and the 2026–2027 CS Multicultural or International
requirement (`t0_row_0018`). Either dated designation can supply credits toward
its three-credit target. A course appears once for this requirement and retains
the matched designations and their classification sources. Coverage under
`by_attribute` shows the evidence for each designation. An incomplete list for
either designation keeps overall coverage partial. The shared audit evaluates documented completed
science options and generates major pair candidates. No term availability, sections, scheduling,
preference ranking or interactive prerequisite clarification is implemented.

The workflow is:

1. Retrieve entered-course attributes and credit evidence before running the
   existing `evaluate_preview()` calculations. With the same input evidence,
   degree progress is identical to `--supabase-attributes` mode.
2. Build/finalize the original handoff. Preflight it through the existing pure
   `build_flexible_candidates()` with no catalog rows. Its existing progress gates
   identify which supported requirements need a catalog query; an unassessed
   requirement is not assumed unfinished. Completed and policy-blocked categories
   do not trigger attribute-candidate queries.
3. Call `load_attribute_candidates()` once for the needed categories, supplying
   the reviewed `catalog_attributes.csv` bytes. Preserve its source reconciliation,
   relationship diagnostics, inactive/uncertain withheld evidence, and bounded
   snapshot coverage. Build the candidates using the same standalone builder.
4. Union the actual flexible candidate memberships with explicit candidates,
   deduplicate by course code, and call the existing prerequisite service once.
   A nonempty pool produces one prerequisite-loader call (internally batched and
   paginated); an empty pool needs no prerequisite SELECTs. Missing-course OR
   diagnostics never add courses to this pool.
5. Attach the results after finalization. Preserve UNMET and UNKNOWN members;
   prerequisites do not erase a course's degree-requirement membership.

No degree progress, choices, course bundles, credit shortfalls, or credit-sharing
policies are recalculated by this projection. The three standalone services
remain reusable and unchanged by this integration.

### Additive fields and references

The handoff has `schema_version: "2.1.0"` and a new `flexible_candidates` section.
The preview JSON includes the same section and `course_eligibility`; the preview's
existing degree-progress fields remain unchanged.

`flexible_candidates` preserves the entire Stage 4A result:

- `catalog_year`, `basis`, `source_verification`, `retrieval_status`, and sanitized
  `retrieval_issues`.
- `requirements[]`, containing degree `requirement_id`, name, category, known
  `remaining_credits_candidate`, per-requirement `generation_status`, reasons,
  `coverage`, `candidates`, `withheld_courses`, and completed-course exclusions.
- Course IDs/codes, matched attributes, classification sources, activity status,
  credit ranges and credit fit. Variable credits never assume the maximum award.
- `skipped_requirements`, deduplicated `candidate_course_codes`, and
  `course_requirement_memberships`. One course can have multiple degree matches;
  `allocation_status` remains `NOT_ALLOCATED_NO_DOUBLE_COUNTING_ASSUMED`.

Each actionable candidate adds:

```json
{
  "course_code": "PSC 201",
  "prerequisite_status": "UNKNOWN",
  "course_eligibility_reference": "PSC 201"
}
```

`course_eligibility_reference` joins by exact course code to
`course_eligibility.courses` within this same catalog-year handoff. That entry
contains the complete prerequisite/corequisite result, grade evidence, rule
provenance and loader diagnostics. The copied status is the combined result;
neither field indicates registration approval. Withheld courses are not evaluated
solely because they appear as withheld evidence, although a course independently
required by an explicit requirement still belongs in the combined pool.

`course_eligibility.candidate_scope` becomes
`EXPLICIT_AND_CORE_II_REQUIREMENT_COURSES`. Its courses are the sorted, deduplicated
union, including the Multicultural or International options in v2.1. Its summary
counts each course once across all requirement memberships. The original
degree `summary` remains unchanged.

The standalone provider's `prerequisites_evaluated: false` is retained in the
flexible block: the provider itself has not evaluated prerequisites. Attached
per-course references point to the separately evaluated evidence. Do not interpret
this provider flag as the execution status of `course_eligibility`.

The integration also adds two block-level fields:

| Field | Meaning |
|---|---|
| `completed_course_attribute_evidence` | `status` AVAILABLE/UNAVAILABLE and sanitized issues for the initial progress lookup. |
| `generation_status` | COMPLETED, NOT_NEEDED, PARTIAL, or UNAVAILABLE for supported category generation. |

COMPLETED means requested supported categories were generated; it does not mean
all degree requirements or prerequisites are satisfied. NOT_NEEDED means no
supported requirement needed a catalog query. PARTIAL includes incomplete progress,
source coverage or withheld candidates. UNAVAILABLE covers failure of the initial
progress lookup, source-file/client setup or attribute-candidate retrieval.

### Coverage and requirement annotations

Existing requirement structures retain their IDs, options, progress and credit
fields. For supported categories only, `eligible_catalog_course_options_included`
is true when the provider reports GENERATED with complete membership coverage.
Partial/withheld enumeration leaves it false. Global
`unmet_attribute_option_lists_enumerated` is true only when every unfinished credit requirement explicitly includes a complete
candidate list. Unsupported or unmapped credit requirements, including Writing Intensive and
Free Electives, keep the flag false while unfinished. A missing inclusion flag counts as false. Existing warnings remain,
with an added supported-scope warning and updated coverage note.

For a flexible requirement, `prerequisites_evaluated` requires complete enumeration,
a nonempty candidate set, and complete prerequisite loader envelopes for every
candidate. UNKNOWN due to grades or unresolved rules can still mean evaluation
was performed. Partial enumeration cannot claim complete requirement evaluation.
`prerequisite_evaluation_status` uses the existing statuses, plus NO_CANDIDATES when
a fully enumerated unfinished requirement has no remaining usable candidates.
Completed categories retain NOT_APPLICABLE; deferred/unassessed categories retain
CANDIDATES_NOT_ENUMERATED. `offered_sections_evaluated` remains false.

### Failure behavior

Expected completed-course attribute failures now have the specific
`AttributeEvidenceUnavailable` subtype of `PreviewError`. In the new mode only,
they produce an audit without attribute evidence and an explicit unavailable
diagnostic. The provider then withholds unassessed category recommendations.
Explicit-course prerequisite evaluation may still succeed independently.
Legacy `--supabase-attributes` failure behavior is unchanged.

Attribute candidate errors preserve already computed degree progress and return
no partial batch labeled complete. Prerequisite errors preserve the flexible
memberships and mark the affected combined pool UNKNOWN. Source hash mismatches
retain the provider's PARTIAL semantics: positive year-scoped database memberships
can remain visible, but exhaustive reviewed-source coverage is not asserted.

Expected availability failures return exit code 0 when a valid audit/report can
be produced, with structured status and console warnings. Malformed grades,
invalid reported credits and programming errors are not converted into availability
success. A report consumer must inspect both generation and prerequisite statuses.

### Schedule Generator coordination

Teammate approval is needed before the scheduler consumes v2.1: accept the expanded
candidate scope, join flexible memberships through requirement IDs, resolve the
course-code eligibility references, preserve choice/bundle structures, and retain
UNKNOWN results without silently accepting them. Category approval does not prove
offerings, open seats, schedule feasibility or permission to double-count credits.
No consumer code or schema migration is changed here. Existing consumers can keep
requesting unchanged v1/v2.0 output until v2.1 support is agreed.

<a id="internal-preview-v22-critical-thinking"></a>

## Opt-in v2.2 (Critical Thinking)

From the repository root, preview the additional CS Core I slot alongside existing Core II options:

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.preview_degree_audit --input backend/scripts/data/degree_requirements/resolved/computer_science_bs_resolved.json --completed "FYS 100,MTH 229" --critical-thinking-candidates --planning-output planning.json
```

Python callers select `run_degree_plan(..., critical_thinking_candidates=True)`. The preview includes Core II and prerequisite evaluation in the same pass and emits `schema_version: "2.2.0"`. HTTP clients select the same handoff by sending the JSON boolean `critical_thinking_candidates: true` to `POST /api/v1/planning/evaluate`. Omitting the field or sending `false` preserves v2.1. The v2.2 handoff uses the candidate scope `EXPLICIT_CORE_II_AND_CRITICAL_THINKING_REQUIREMENT_COURSES`.

- **Open slot.** For the CS B.S. 2026–2027 rule `t0_row_0005`, the audit must establish a positive shortfall. A prospective course needs an active, year-scoped CT membership reconciled to the reviewed catalog export and enough catalog credits for the single course slot. Inspect `generation_status`, `coverage`, and `withheld_courses` when reads or classifications are incomplete.
- **Distinct courses.** The builder uses the audit's `reserved_explicit_sibling_courses` when reconciling entered coursework and listing options. FYS 100 and MTH 229 remain available to their explicit rules. Completing MTH 229 provisionally meets both its Core I and major occurrences. Completing ANT 201 as well can fill the additional CT slot and match Social Sciences. Same-group reservation never consumes the course globally.
- **Consumer joins.** An uncompleted ANT 201 can be an alternative for both `t0_row_0005` and `t0_row_0013`. Both memberships reference the same course-level prerequisite result. MET, UNMET, and UNKNOWN describe student-reported prerequisite evidence. Each entry retains its classification sources. Cross-requirement credits remain unallocated.
- **Academic review.** The standard draft lacks admission category, transfer-waiver evidence, and where a completed course was taken. The builder withholds MTH 229H from the additional slot pending confirmation of distinct credit with MTH 229. When completion depends on MTH 229H, the audit and the v2.2 candidate list both report `INSUFFICIENT_EVIDENCE` with `HONORS_DISTINCT_SLOT_REVIEW_REQUIRED`. The audit retains the dated classification and potential credit subtotal, and leaves the credit shortfall unknown. Another entered CT course with sufficient known credits can independently fill the slot. The v2.1 API uses the same corrected audit and keeps its existing schema and candidate scope.

## Reusable candidate-provider interface

The service's interface is independent of requirement kinds:

```python
evaluate_candidate_prerequisites(
    client,
    candidate_course_codes,
    completed_courses,
    catalog_year=degree_catalog_year,
    source_catalog_csv=reviewed_catalog_bytes,
    source_prerequisite_csv=reviewed_prerequisite_bytes,
    concurrent_courses=None,
)
```

The Core II provider retrieves approved, catalog-year-scoped courseattribute
matches for each remaining category and preserves category/requirement membership
in a separate mapping. The integration combines its actual course codes with explicit candidates.
Deduplicate that union for one service call, then associate results back with every
requirement the course can satisfy. Do not create a pseudo-course named "Core II
Humanities". Attribute classification establishes category membership; prerequisite
MET establishes only reported rule evidence. Neither settles degree allocation,
substitution policy, offered sections or registration eligibility.

`apply_prerequisite_results()` checks the explicit pool by default and the validated
union when its optional `flexible_candidates` argument is supplied. New providers
must preserve that membership validation and coordinate any scope extension with
the scheduler. The prerequisite service needs no category-specific branch.

## Validation and next integration boundary

Offline tests run with:

```text
.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -p "test_*.py"
```

Stage 3 adds v1 fixture characterization, input compatibility, unchanged progress,
v2 projection, expected failures, and real loader/evaluator composition with a
SELECT-only fake client. Optional live/staging scripts are separate and are not
part of offline discovery.

Stage 4B adds mocked CLI-to-service composition using the real attribute loader,
candidate builder, prerequisite loader and evaluator, with a SELECT-only fake SDK.
Tests cover v1/v2 compatibility, the combined batch, v2.1 projections, source and
service failures, grade uncertainty, overlap, completed categories and unchanged
degree-progress calculations. No live verification is part of this suite.

Before the Schedule Generator consumes v2, agree on version support, UNKNOWN
handling, branch diagnostics, concurrency evidence, and incomplete attribute
coverage. It must still check offerings, sections, conflicts, seats, registration
restrictions and reported evidence. No scheduler behavior is changed by Stage 3.
