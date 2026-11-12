# Development handoff

## Current state · 2026-11-11

The local degree planning API is merged in `16c1d41` (PR #10). It evaluates student-reported coursework for Computer Science, B.S., catalog year `2026-2027`, and returns the v2.1 handoff. Frontend consumption and Schedule Generator integration are pending. [API guide](planning_api.md) · [Architecture and service interfaces](project_context.md)

This handoff records the science-specific worktree on `feature/science-lab-requirements`, based on `d889101`.

The CT preview is isolated on `feature/critical-thinking-isolated`, based on `f04a47e`. The service and CLI support the additional CS Critical Thinking slot through `--critical-thinking-candidates`. The public API still returns v2.1.

The current working tree supports the listed science pairs and PS 111 under the 2026–2027 CS working rules. The shared audit can recognize a documented four-credit Core II lab option and omit redundant major science candidates. The v2.1 API serves these results through its existing evaluation route. [Science and laboratory requirements](project_context.md#science-and-laboratory-requirements)

## Completed milestones and recent development history

| Date | Change and evidence | Verification and remaining limits |
| --- | --- | --- |
| 2026-10-12 to 2026-10-15 | Schedule capture and catalog attribute imports (`b695d9a`, `56603e0`), degree parsing (`c13f9b6`), and the local audit preview (`db60b16`). The resolver preserves source rows and review flags in the CS draft. | Tracked section/meeting exports and the resolved JSON document the inputs. The draft lists 30 ready rules and nine for review. These are artifact counts. |
| 2026-11-03 to 2026-11-08 | Frontend Supabase sign-in and course browsing, course details, term-based course selection, and catalog fallback. The public schema snapshot was added in `182d541`. | The frontend queries courses and offerings directly. Semester selections are temporary. The supplied schema omits deployment permissions and auth details. |
| 2026-11-09 | Prerequisite parsing/import tooling (`a3dce7e`), SELECT-only loader and pure evaluator (`28a60e8`), then v2.0 explicit/choice candidate eligibility (`8512b70`). The loader checks a dated rule graph against reviewed source files. | The pipeline guide records a 53-test offline revision and an earlier five-test disposable PostgreSQL run. Of 47 explicit CS courses in its coverage review, 27 had representable rules, 11 had unresolved conditions, and nine lacked published metadata. [Pipeline results](../backend/scripts/PREREQUISITE_PIPELINE.md#implementation-validation-results) |
| 2026-11-10 | Core II provider (`e4c8fd5`) and v2.1 integration (`1dc7a23`). One prerequisite batch covers explicit courses, choice options, and supported flexible memberships. | Fixture tests exercise coverage, withheld evidence, overlap, and v1/v2.0 compatibility. Flexible enumeration covers Humanities, Social Sciences, and Fine Arts. [Integration tests](../backend/tests/test_core_ii_planning.py) |
| 2026-11-10 | Shared service (`0eb6dfc`), centralized Supabase client (`c05e37a`), and FastAPI endpoints (`0e92c38`, merged in `16c1d41`). The API selects v2.1 and shares one request-scoped client across readers. | All 288 offline backend tests passed on this checkout at `16c1d41`. HTTP tests compare the complete response with a direct service call using a SELECT-only fixture. The team separately reports five successful live HTTP evaluations against Supabase. No committed run report for those five was located, and this documentation task used no live connection. [HTTP tests](../backend/tests/test_planning_api.py) |
| 2026-11-11 | Stage 7 extended the internal service, CLI, and flexible candidate builder for CS rule `t0_row_0005`. Internal v2.2 reserves explicit sibling courses locally and reuses the shared prerequisite batch. | Full pytest suite passed (295 tests, 352 subtests). The offline replay of 83 dated CT memberships produced 81 options with empty coursework. MTH 229 was reserved and MTH 229H withheld for review. Legacy v1, v2.0, and v2.1 preview and handoff outputs matched the base revision on the same fixture. [Preview contract](planning_handoff.md#internal-preview-v22-critical-thinking) |
| 2026-11-11 | Science with Lab. The audit reuses the major's complete, nonoverlapping pairs, checks dated classifications and credits for one Core II option, and excludes redundant physics or fourth-choice candidates from prerequisite evaluation. PS 111 can fill Core II independently. | Ten new offline tests cover the listed pairs, shared lab, duplicates, missing evidence, substitutions, candidate membership, prerequisite diagnostics, and HTTP/service parity. In the isolated worktree, pytest passed 298 tests and 353 subtests with one Starlette test-client deprecation warning. Unittest passed all 298 tests. No live database was used. [Science tests](../backend/tests/test_science_lab_planning.py) |
| 2026-11-11 | MTH 229H. For the additional CS Core I slot, the shared audit and CT candidate generator report insufficient evidence when completion depends on the honors course. The dated classification and potential credits remain visible. Another qualifying course can independently meet the slot. | Pytest passed 307 tests and 362 subtests with one Starlette test-client deprecation warning. Unittest passed all 307 tests. Three offline HTTP fixture evaluations confirmed the corrected audit under the existing v2.1 schema. [Handoff contract](planning_handoff.md#internal-preview-v22-critical-thinking) |

## Upcoming development

| Area | Open work or decision |
| --- | --- |
| Science with Lab | Confirm the provisional major/Core II overlap with the program. Obtain catalog-year evidence for additional Core II labs and approved “or above” substitutes before extending coverage. |
| Critical Thinking | Review the internal v2.2 consumer contract and determine what evidence is needed for honors variants, transfer exemptions, and coursework origin. |
| Prerequisites | Design interactive clarification for UNKNOWN results, including missing grades or equivalency evidence. Preserve uncertainty when catalog metadata itself is incomplete. |
| Credit allocation | Resolve repeated credit slots, variable-credit evidence, and overlap across major and core requirements. Preserve potential matches until allocation policy is reviewed. |
| Frontend and scheduling | Connect the frontend to the planning API. Agree with the scheduler owner on handoff version, choice bundles, flexible memberships, UNKNOWN results, and term-specific feasibility checks. |
| Full application and deployment | Exercise the student workflow end to end. Define and implement backend authentication and authorization, scheduler access, deployed origins, and operational limits. Browser sign-in already exists. |
| RAG Advisor | Connect reviewed source material to retrieval and answers. The current Advisor page is a preview. |

## Next recommended action

For the science progress report, demonstrate the v2.1 audit with a listed pair, PS 111 alone, and three complete pairs. Use the existing source flags to distinguish provisional planning from institutional approval. [Science handoff contract](planning_handoff.md#cs-science-with-lab-progress-across-handoff-versions)

Confirm with the CS program whether MTH 229H can independently satisfy the additional CT slot, then review a v2.2 handoff with the frontend and Schedule Generator owners. Confirm how each consumer presents alternative CT courses, shared memberships, withheld honors evidence, and UNKNOWN prerequisites. Agree on an explicit API version or request option before exposing CT to HTTP clients.
