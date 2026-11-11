# Development handoff

## Current state · 2026-11-10

The local degree planning API is merged in `16c1d41` (PR #10). It evaluates student-reported coursework for Computer Science, B.S., catalog year `2026-2027`, and returns the v2.1 handoff. Frontend consumption and Schedule Generator integration are pending. [API guide](planning_api.md) · [Architecture and service interfaces](project_context.md)

This handoff records the 2026-11-10 checkout of `feature/science-lab-requirements`, created at `16c1d41`. Contributors should use the branch appropriate to their own task.

The next degree-planning objective is to investigate the four-credit Core II science requirement and the major-specific Science w/ Lab choice group. Core II lab candidate generation is pending. The audit already represents the major's listed lecture/lab pairs. Their relationship to Core II remains unverified. [Science and laboratory requirements](project_context.md#science-and-laboratory-requirements)

## Completed milestones and recent development history

| Date | Change and evidence | Verification and remaining limits |
| --- | --- | --- |
| 2026-10-12 to 2026-10-15 | Schedule capture and catalog attribute imports (`b695d9a`, `56603e0`), degree parsing (`c13f9b6`), and the local audit preview (`db60b16`). The resolver preserves source rows and review flags in the CS draft. | Tracked section/meeting exports and the resolved JSON document the inputs. The draft lists 30 ready rules and nine for review. These are artifact counts. |
| 2026-11-03 to 2026-11-08 | Frontend Supabase sign-in and course browsing, course details, term-based course selection, and catalog fallback. The public schema snapshot was added in `182d541`. | The frontend queries courses and offerings directly. Semester selections are temporary. The supplied schema omits deployment permissions and auth details. |
| 2026-11-09 | Prerequisite parsing/import tooling (`a3dce7e`), SELECT-only loader and pure evaluator (`28a60e8`), then v2.0 explicit/choice candidate eligibility (`8512b70`). The loader checks a dated rule graph against reviewed source files. | The pipeline guide records a 53-test offline revision and an earlier five-test disposable PostgreSQL run. Of 47 explicit CS courses in its coverage review, 27 had representable rules, 11 had unresolved conditions, and nine lacked published metadata. [Pipeline results](../backend/scripts/PREREQUISITE_PIPELINE.md#implementation-validation-results) |
| 2026-11-10 | Core II provider (`e4c8fd5`) and v2.1 integration (`1dc7a23`). One prerequisite batch covers explicit courses, choice options, and supported flexible memberships. | Fixture tests exercise coverage, withheld evidence, overlap, and v1/v2.0 compatibility. Flexible enumeration covers Humanities, Social Sciences, and Fine Arts. [Integration tests](../backend/tests/test_core_ii_planning.py) |
| 2026-11-10 | Shared service (`0eb6dfc`), centralized Supabase client (`c05e37a`), and FastAPI endpoints (`0e92c38`, merged in `16c1d41`). The API selects v2.1 and shares one request-scoped client across readers. | All 288 offline backend tests passed on this checkout at `16c1d41`. HTTP tests compare the complete response with a direct service call using a SELECT-only fixture. The team separately reports five successful live HTTP evaluations against Supabase. No committed run report for those five was located, and this documentation task used no live connection. [HTTP tests](../backend/tests/test_planning_api.py) |

## Upcoming development

| Area | Open work or decision |
| --- | --- |
| Science with Lab | Verify Core II lab eligibility, major-specific equivalents and shared-lab rules, and whether coursework may count toward both requirements. |
| Critical Thinking | Extend beyond the existing same-parent distinct-course check. Establish eligible candidates and rules for allocating the remaining slots. |
| Prerequisites | Design interactive clarification for UNKNOWN results, including missing grades or equivalency evidence. Preserve uncertainty when catalog metadata itself is incomplete. |
| Credit allocation | Resolve repeated credit slots, variable-credit evidence, and overlap across major and core requirements. Preserve potential matches until allocation policy is reviewed. |
| Frontend and scheduling | Connect the frontend to the planning API. Agree with the scheduler owner on handoff version, choice bundles, flexible memberships, UNKNOWN results, and term-specific feasibility checks. |
| Full application and deployment | Exercise the student workflow end to end. Define and implement backend authentication and authorization, scheduler access, deployed origins, and operational limits. Browser sign-in already exists. |
| RAG Advisor | Connect reviewed source material to retrieval and answers. The current Advisor page is a preview. |

## Next recommended action

Trace `t0_row_0011` (Core II, four credits) and `t0_row_0058` (major, three options and 12 credits) to their respective 2026–2027 catalog rows. For Core II, identify what evidence confirms the lab alongside the science attribute. For the major, check the five listed pairs, the “or above” wording, and the shared PHY 202 lab. Look for an explicit policy on applying the same coursework to both rules. Record a supported example and any unresolved case before proposing changes to candidate generation or credit allocation. [Resolved CS draft](../backend/scripts/data/degree_requirements/resolved/computer_science_bs_resolved.json) · [Candidate generator](../backend/services/flexible_candidates.py)
