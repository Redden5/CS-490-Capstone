# Degree planning handoff: v1 and opt-in v2

The handoff is unofficial planning evidence, not an approved degree audit,
verified transcript, graduation determination, or registration clearance.

## Execution and compatibility

`backend/scripts/preview_degree_audit.py` keeps `evaluate_preview()` unchanged.
It builds the handoff with `build_planning_handoff()`, whose
`finalize_planning_handoff_v1()` creates explicit candidate placeholders. Optional
prerequisite enrichment happens **after** that finalizer returns.

Without `--evaluate-prerequisites`, legacy code-list inputs produce the same
preview and `1.0.0` handoff. No prerequisite source files, environment loading,
client creation or queries occur. The existing independent `--supabase-attributes`
option retains its behavior. Neither option is implied by the other.

With `--evaluate-prerequisites`, the CLI:

1. Validates coursework and the draft's catalog-year label.
2. Computes the same degree-progress results and explicit candidate pool.
3. Reads `backend/scripts/courses.csv` and `backend/scripts/catalog_prerequisites.csv`.
4. Loads the root `.env` with `override=False` and creates a dedicated client using
   the existing `create_api_client()` factory. Credentials stay at this CLI boundary.
5. Calls `backend/services/planning_eligibility.py` once for the whole candidate batch.
6. Attaches results to a `2.0.0` handoff and, if requested, the preview JSON.

The opt-in flag works without either output path; the console reports counts and
availability. `--output` includes `course_eligibility` and additional warnings.
`--planning-output` includes the full v2 projection. Existing saved examples are
not upgraded automatically. The frontend and Schedule Generator are unchanged.

The client factory accepts `SUPABASE_URL` and `SUPABASE_SECRET_KEY`, with
`SUPABASE_SERVICE_ROLE_KEY` as its existing fallback. No administrative import or
RPC helper is called. The adapter and loader only retrieve data; no writes occur.

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

**Grades currently affect prerequisite evidence only.** The legacy degree audit
still counts entered course codes, including reported failures, and excludes
entered codes from remaining candidates. It does not generate retake candidates
or establish that those entries earn graduation credit. V2 reports this limitation
and sets `grades_used_for_degree_progress: false`.

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
candidate options are **not generated in this stage**. Existing
`candidate_option_coverage` and attribute-option warnings remain unchanged.
`NO_CANDIDATES` means no explicit candidate codes, not that every flexible
requirement is complete or that the degree has no remaining work.

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

## Future Core II candidate provider

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

A future provider should retrieve approved, catalog-year-scoped courseattribute
matches for each remaining category, preserve category/requirement membership in a
separate mapping, and combine its actual course codes with explicit candidates.
Deduplicate that union for one service call, then associate results back with every
requirement the course can satisfy. Do not create a pseudo-course named "Core II
Humanities". Attribute classification establishes category membership; prerequisite
MET establishes only reported rule evidence. Neither settles degree allocation,
substitution policy, offered sections or registration eligibility.

The present `apply_prerequisite_results()` intentionally checks that results match
the v1 explicit candidate pool. When adding a provider, extend candidate assembly
and that projection together, update `candidate_scope` and coverage only after
successful category enumeration, and coordinate the new handoff with the scheduler.
The evaluation service itself needs no category-specific branch or refactor.

## Validation and next integration boundary

Offline tests run with:

```text
.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -p "test_*.py"
```

Stage 3 adds v1 fixture characterization, input compatibility, unchanged progress,
v2 projection, expected failures, and real loader/evaluator composition with a
SELECT-only fake client. Optional live/staging scripts are separate and are not
part of offline discovery.

Before the Schedule Generator consumes v2, agree on version support, UNKNOWN
handling, branch diagnostics, concurrency evidence, and incomplete attribute
coverage. It must still check offerings, sections, conflicts, seats, registration
restrictions and reported evidence. No scheduler behavior is changed by Stage 3.
