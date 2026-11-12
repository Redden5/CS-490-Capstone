# Marshall University Intelligent Course Advisor Assistant

This CS 490 senior capstone helps students answer two questions. Which degree requirements could their completed courses satisfy, and which courses could they plan next? The implemented planning path covers the Computer Science, B.S. for catalog year `2026-2027`. Students report successfully completed coursework. The system has no DegreeWorks connection and produces an unofficial planning handoff. See [development status](development_status.md) for current work.

## Components and team responsibilities

| Component | Responsibility and integration point |
| --- | --- |
| Academic data | Scrapers, parsers, and resolvers under `backend/scripts/` produce catalog, prerequisite, degree, and offering artifacts. Database import is a separate operator workflow. |
| Degree Planning Engine | The Python backend evaluates the degree, identifies remaining options, and attaches catalog and prerequisite evidence. The CLI and FastAPI share one implementation. |
| Student frontend | React/Vite pages use a publishable Supabase client for sign-in, course browsing, course details, and a temporary semester planner. Academic Info submits reported coursework to the existing planning API; Degree Audit displays its v2.1 requirement progress, choice bundles, flexible options, and prerequisite evidence. The Advisor page still displays sample content. |
| Schedule Generator | The scheduler checks term offerings, sections, seats, and time conflicts. Its intended input is the versioned planning handoff. A schedule-service prototype exists on the separate `origin/feature/schedule-service` tracking ref. Integration with this checkout is pending. |
| RAG Advisor (planned) | The intended advisor would retrieve source material to ground answers to student questions. The schema includes source documents and chunks. This checkout has no retrieval or answer-generation service. |

Coordinate contract changes with the teammates responsible for the affected components. The handoff guide calls for scheduler agreement on version, candidate membership, and treatment of uncertainty before adoption. The tracked project guides identify component boundaries but do not assign individual owners. [Schedule Generator coordination](planning_handoff.md#schedule-generator-coordination)

## Local development

Use PowerShell from the repository root with the Python 3.12 virtual environment and npm dependencies installed. Run the two servers in separate terminals. The root npm package supplies the Supabase SDK, and the frontend package supplies Vite. [Backend setup](planning_api.md#run-locally) · [Frontend setup](../frontend/README.md)

| Task | Command from repository root |
| --- | --- |
| Start FastAPI | `.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload` |
| Start React/Vite | `npm --prefix frontend run dev` |
| Run the offline backend regression suite | `.\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -p "test_*.py"` |

For live catalog reads, the backend accepts `SUPABASE_URL` and `SUPABASE_SECRET_KEY` (fallback `SUPABASE_SERVICE_ROLE_KEY`) from the server environment or the root `.env`. Offline tests use fixtures. The frontend requires `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY`. Keep privileged keys server-side. [Backend client](../backend/services/supabase_service.py) · [Browser client](../frontend/src/lib/supabaseClient.js)

The frontend planning client defaults to `http://127.0.0.1:8000` in Vite development and same-origin API paths in production builds. `VITE_PLANNING_API_URL` overrides the API origin. Existing backend CORS and authentication limits still apply. [Frontend configuration](../frontend/README.md#academic-info-and-degree-audit)

| Directory | Contents |
| --- | --- |
| `backend/` | FastAPI entry point, HTTP routes and models, planning services, and regression tests in `backend/tests/`. |
| `backend/scripts/` and `backend/migrations/` | Catalog and degree data tools, source exports and resolved drafts, and SQL changes for the prerequisite schema. |
| `frontend/src/` | React pages, shared components, styles, and the browser Supabase client. |
| `docs/` | Project handoff, planning contracts, and public schema snapshots in `docs/database/`. |

## From student input to planning handoff

1. The CLI accepts a resolved draft and coursework. The HTTP route selects a draft from a fixed program/year registry. Both call `run_degree_plan(draft, completed_courses, ...)`, which returns a `DegreePlanResult` with `preview` and `handoff`. The service normalizes course identities and retains individual attempts, grades, and reported equivalency evidence for prerequisite checks. [CLI entry point](../backend/scripts/preview_degree_audit.py) · [Shared planning service](../backend/services/degree_planning.py)
2. The audit reads the [resolved CS degree JSON](../backend/scripts/data/degree_requirements/resolved/computer_science_bs_resolved.json). This local draft is the runtime source even though the database schema can represent degrees. `evaluate_preview()` compares distinct entered codes with required courses, evaluates choice-group progress, and uses available attribute and credit evidence for applicable thresholds. The draft records 39 source rules, including nine marked for review. `build_planning_handoff()` preserves requirement IDs and all-course bundles. Its v1 finalizer collects missing explicit courses and missing members of unfinished choice options, excluding entered courses. [Degree audit](../backend/services/degree_audit.py)
3. In Core II mode, `load_attribute_candidates()` reads year-scoped catalog associations and checks them against the reviewed attribute export. The pure `build_flexible_candidates()` enumerates Fine Arts, Humanities, and Social Sciences (called Social Studies in the draft) only when the requirement has a known shortfall and sufficient policy evidence. It preserves course-to-requirement membership, withheld courses, and reasons for incomplete coverage. [Flexible candidate generator](../backend/services/flexible_candidates.py)
4. `evaluate_candidate_prerequisites()` evaluates the deduplicated candidate pool once. `load_prerequisite_rules()` resolves year-specific database rows to exact course codes and reconciles them with the reviewed source files. The pure `evaluate_course_rules()` applies the rule logic to reported coursework. The projection attaches results by course code and keeps every applicable requirement membership, including when eligibility is UNMET or UNKNOWN. [Eligibility adapter](../backend/services/planning_eligibility.py) · [Loader](../backend/services/prerequisite_loader.py) · [Evaluator](../backend/services/prerequisite_evaluator.py)

### Service and HTTP compatibility

| Handoff schema | How callers select it | Evidence returned |
| --- | --- | --- |
| `1.0.0` | Default service mode | Degree progress and explicit/choice candidates with unevaluated eligibility placeholders. Default inputs need no database access. The independent `supabase_attributes` option can add entered-course evidence. |
| `2.0.0` | `evaluate_prerequisites=True` | Adds prerequisite evaluations for explicit and choice candidates. Preserves their degree dispositions. |
| `2.1.0` | `core_ii_candidates=True` | Implies entered-course attribute retrieval and prerequisite evaluation. Adds flexible memberships and a combined eligibility pool. |

The FastAPI app at `backend.main:app` exposes `GET /api/v1/planning/programs` and `POST /api/v1/planning/evaluate`. The latter selects v2.1 and returns the handoff directly. A successful response uses `Cache-Control: no-store`. Submitted coursework stays in request memory. A consumer can join flexible results by requirement ID and eligibility by course code. Check per-requirement coverage and retrieval diagnostics even on HTTP 200. [App](../backend/main.py) · [Routes and registry](../backend/routes/planning.py) · [Request and response models](../backend/models/planning.py) · [API guide](planning_api.md) · [Handoff contract](planning_handoff.md)

Academic Info discovers supported program/year combinations from that API. It accepts separate completed-course attempts, optional grades and reported credits, and unresolved-equivalency flags. A React provider shares input and the last successful report across authenticated routes. Edits cancel pending requests and mark old results as outdated; refresh, sign-out, or account changes clear the state. No student coursework is written to Supabase or browser storage. Degree Audit presents backend decisions rather than reimplementing academic rules, preserves all-course bundles and uncertain evidence, and labels credit subtotals as estimates. Planner and Advisor integration remain separate work. [API client](../frontend/src/lib/planningApi.js) · [Shared state](../frontend/src/context/PlanningProvider.jsx) · [Audit page](../frontend/src/pages/DegreeAudit.jsx)

## Database and academic evidence

Supabase hosts PostgreSQL. The November 8 public schema exports describe tables and relationships as supplied on that date. Later prerequisite code expects dated rules, review flags, and source fingerprints described by migration 001. Consult the migration and matching reader when working on that graph. Live schema, permissions, and imported row counts require separate evidence. [Schema and snapshots](database/README.md) · [Catalog-year migration](../backend/migrations/001_course_rules_catalog_year.sql)

| Data | Tables and interpretation |
| --- | --- |
| Catalog | `department` and `course` identify subjects, exact course codes, titles, and credit ranges. `courseattribute` links a course to an `attribute` for a `catalogyear` with source metadata. Use the degree draft's year. |
| Prerequisites and corequisites | `courserule` holds the rule parent. `ruleclause` holds alternatives. `rulecourse` identifies the course atoms in a clause, with grade and concurrency fields. For reviewed machine-checkable rules, all atoms in one clause must hold and one alternative suffices. Preserve unresolved parent text and review flags. |
| Degree representation | `program`, `degreetemplate`, hierarchical `degreerequirement`, `requirementcourse`, and `requirementattribute` can express program/year structures. The reviewed planning route loads the local resolved JSON. |
| Term offerings | `term` and `coursesection` establish when a course is offered. `sectionmeeting` describes meeting times and places, and `sectionattribute` describes a section. A catalog match alone gives no evidence of an offered section, open seat, or feasible timetable. |
| Advising and student data | `sourcedocument` and `documentchunk` describe potential retrieval material. The supplied public schema lists no saved student coursework, semester plan, or conversation tables. |

The reviewed 2026–2027 source manifest records 6,692 distinct course identities and 1,935 courses with prerequisite-related metadata. Coverage refers to that capture. The repository also contains catalog attribute exports, term sections and meetings, and raw/parsed degree artifacts. Only the resolved CS program/year is registered in the API. [Snapshot manifest](../backend/scripts/data/prerequisites/investigation/snapshot_manifest.json) · [Prerequisite pipeline and validation](../backend/scripts/PREREQUISITE_PIPELINE.md)

### Science and laboratory requirements

The resolved 2026–2027 CS draft has two science rules with different scopes. Both carry review flags. [Resolved CS degree draft](../backend/scripts/data/degree_requirements/resolved/computer_science_bs_resolved.json)

| Rule | What the draft establishes | Open policy question |
| --- | --- | --- |
| Core II Physical/Natural Science (`t0_row_0011`) | Four-credit requirement mapped to `Core II Natural Sciences`. The draft flags `lab_eligibility_unverified`. | What catalog-year evidence confirms the lab condition for a course or combination? |
| Major-Specific Science w/ Lab (`t0_row_0058`) | Choose three lecture/lab options with a 12-credit group target. Each listed pair is an `ALL_OF` option: BSC 120 & BSC 120L, CHM 211 & CHM 217, GLY 200 & GLY 210L, PHY 201 & PHY 202, or PHY 211 & PHY 202. | Which “or above” equivalents qualify, and how may the shared PHY 202 lab count across the two physics options? |

The audit already tracks listed choice bundles and computes nonoverlapping progress within a group. Whether coursework can count toward both the major and Core II requires separate policy evidence. [Degree audit](../backend/services/degree_audit.py)

## Decisions and limits to preserve

- **Reported coursework.** Degree progress uses distinct entered codes. Prerequisite evaluation uses richer evidence including optional grades. Missing minimum-grade evidence, conflicting attempts, unverified equivalencies, and unreviewed corequisite timing can yield UNKNOWN. Preserve suffixes and hyphens in course identities.
- **Three-valued eligibility.** MET means reported evidence satisfies the evaluated rule, UNMET means the evaluated condition fails, and UNKNOWN means the evidence cannot settle it. Unpublished, undated, or incompletely retrieved rules cannot clear a course. Keep source URLs, rule/clause provenance, review flags, and coverage diagnostics. A missing course from an unused OR branch must never become a mandatory schedule addition.
- **Database and security.** Planning uses SELECT-only readers and one injected or lazily created client per evaluation. The backend factory owns privileged credential handling. The browser uses its publishable key. Frontend sign-in exists, while the planning API currently serves unauthenticated local development. Shared deployment requires backend authentication and authorization. [Server client](../backend/services/supabase_service.py) · [Supabase import workflow](../backend/scripts/PREREQUISITE_SUPABASE.md)
- **Academic policy gaps.** Critical Thinking has a narrow same-parent rule reserving explicit sibling courses for distinct Core I slots. General Critical Thinking candidate generation, repeated credit-slot allocation, and decisions about credit sharing across requirements remain open. Retain review flags for unsupported policies and distinguish potential credit progress from awarded credit.
- **Consumer boundary.** Planning supplies candidate evidence. Scheduling owns term feasibility. Interactive clarification of UNKNOWN evidence, integrated scheduling, and retrieval-backed advising remain future work. Existing consumers can continue selecting v1 or v2.0 when extending the v2.1 contract.
