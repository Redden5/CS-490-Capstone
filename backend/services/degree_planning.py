"""Read-only orchestration for the degree audit and versioned planning handoff."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, NamedTuple

from backend.services import supabase_service
from backend.services.degree_audit import (
    AttributeEvidenceUnavailable, PreviewError, apply_prerequisite_results,
    build_planning_handoff, canonical_course_code, combined_candidate_codes,
    course_credit_evidence, coursework_codes, evaluate_preview, normalize_coursework,
)
from backend.services.planning_eligibility import validate_candidate_evidence


_SOURCE_DIR = Path(__file__).resolve().parents[1] / "scripts"


class _PlanningClient:
    """One lazy client per evaluation, including at most one configuration attempt."""

    def __init__(self, client, client_factory):
        self.client = client
        self.client_factory = client_factory
        self.failure: type[Exception] | None = None

    def get(self):
        if self.failure is not None:
            raise self.failure("Supabase client unavailable for this evaluation")
        if self.client is None:
            try:
                self.client = self.client_factory()
            except (ImportError, OSError, ValueError, RuntimeError) as error:
                self.failure = ImportError if isinstance(error, ImportError) else RuntimeError
                raise
        return self.client


def _read_client(client, client_factory):
    if client is not None:
        return client
    return (client_factory or supabase_service.create_supabase_client)()


def fetch_supabase_attribute_evidence(
    completed_courses: list[str], catalog_year: str,
    reported_credits: dict[str, float] | None = None, *, client: Any = None,
    client_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Fetch only the student's entered courses and catalog-year attributes.

    This path executes SELECTs only. It deliberately excludes unversioned
    courseattribute rows and sectionattribute rows: neither establishes a
    course-level classification for the chosen catalog year.
    """
    try:
        db = _read_client(client, client_factory)
    except ImportError:
        raise AttributeEvidenceUnavailable(
            "Supabase mode requires installed 'supabase' and 'python-dotenv' packages."
        ) from None
    except (OSError, ValueError, RuntimeError):
        raise AttributeEvidenceUnavailable(
            "Cannot initialize Supabase. Check the server URL and secret or service-role key."
        ) from None

    def fetch_rows(build_query: Callable[[], Any], description: str) -> list[dict[str, Any]]:
        """Page SELECT results to avoid PostgREST's default response limit."""
        rows: list[dict[str, Any]] = []
        page_size = 500
        query = None
        while True:
            try:
                if query is None:
                    query = build_query()
                response = query.range(len(rows), len(rows) + page_size - 1).execute()
            except Exception as error:
                raise AttributeEvidenceUnavailable(
                    f"Supabase SELECT for {description} failed ({type(error).__name__}). "
                    "Check schema access, table permissions, and credentials."
                ) from None
            batch = response.data
            if not isinstance(batch, list):
                raise AttributeEvidenceUnavailable(f"Unexpected Supabase response for {description}.")
            rows.extend(batch)
            if len(batch) < page_size:
                return rows

    years = fetch_rows(
        lambda: db.table("catalogyear")
          .select("catalog_year_id,catalog_label")
          .eq("catalog_label", catalog_year),
        "catalogyear",
    )
    if len(years) != 1:
        raise AttributeEvidenceUnavailable(
            f"Expected one catalogyear record for {catalog_year!r}; found {len(years)}."
        )
    year_id = years[0]["catalog_year_id"]

    completed = sorted(set(completed_courses))
    courses: list[dict[str, Any]] = []
    for index in range(0, len(completed), 100):
        courses.extend(fetch_rows(
            lambda: db.table("course")
              .select("course_id,course_code,min_credit_hours,max_credit_hours")
              .in_("course_code", completed[index:index + 100]),
            "course",
        ))
    credit_by_course = course_credit_evidence(completed, courses, reported_credits)
    code_by_id = {row["course_id"]: row["course_code"] for row in courses}
    course_ids = sorted(code_by_id)

    associations: list[dict[str, Any]] = []
    for index in range(0, len(course_ids), 100):
        associations.extend(fetch_rows(
            lambda: db.table("courseattribute")
              .select("course_id,attribute_id,source_url,observed_at")
              .eq("catalog_year_id", year_id)
              .in_("course_id", course_ids[index:index + 100]),
            "catalog-year courseattribute",
        ))

    attribute_ids = sorted({row["attribute_id"] for row in associations})
    attrs: list[dict[str, Any]] = []
    for index in range(0, len(attribute_ids), 100):
        attrs.extend(fetch_rows(
            lambda: db.table("attribute")
              .select("attribute_id,attribute_name")
              .in_("attribute_id", attribute_ids[index:index + 100]),
            "attribute",
        ))
    attribute_by_id = {row["attribute_id"]: row["attribute_name"] for row in attrs}
    missing_attribute_ids = set(attribute_ids) - set(attribute_by_id)
    if missing_attribute_ids:
        raise AttributeEvidenceUnavailable(f"Missing attribute definitions for IDs: {sorted(missing_attribute_ids)}")

    courses_by_attribute: dict[str, set[str]] = {}
    # [attribute_name][course_code] -> provenance from year-scoped source rows
    sources: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in associations:
        name = attribute_by_id[row["attribute_id"]]
        code = canonical_course_code(code_by_id[row["course_id"]])
        courses_by_attribute.setdefault(name, set()).add(code)
        sources.setdefault(name, {}).setdefault(code, []).append({
            "source_url": row["source_url"],
            "observed_at": row["observed_at"],
        })

    return {
        "catalog_year": catalog_year,
        "catalog_year_id": year_id,
        "credit_by_course": credit_by_course,
        "courses_by_attribute": courses_by_attribute,
        "sources": sources,
        "entered_courses_missing_from_course_table": sorted(
            set(completed) - {canonical_course_code(code) for code in code_by_id.values()}
        ),
        "courses_with_no_year_scoped_attribute": sorted(
            set(completed) & {canonical_course_code(code) for code in code_by_id.values()}
            - {code for codes in courses_by_attribute.values() for code in codes}
        ),
        "year_scoped_associations_retrieved": len(associations),
    }


def fetch_planning_eligibility(
    handoff: dict[str, Any], coursework: list, *, candidate_course_codes: list[str] | None = None,
    client: Any = None, client_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Read-only optional evidence. Expected availability failures keep the audit usable."""
    from backend.services.planning_eligibility import (
        evaluate_candidate_prerequisites, unavailable_candidate_prerequisites,
    )

    codes = ([c["course_code"] for c in handoff["course_eligibility"]["courses"]]
             if candidate_course_codes is None else sorted(set(candidate_course_codes)))
    year = handoff["degree"]["catalog_year"]
    # Do not hide malformed input behind missing credentials/source artifacts.
    validate_candidate_evidence(codes, coursework, catalog_year=year)
    if not codes:
        return evaluate_candidate_prerequisites(None, codes, coursework, catalog_year=year)
    try:
        catalog = (_SOURCE_DIR / "courses.csv").read_bytes()
        prerequisites = (_SOURCE_DIR / "catalog_prerequisites.csv").read_bytes()
    except OSError:
        return unavailable_candidate_prerequisites(
            codes, coursework, catalog_year=year, reason="SOURCE_ARTIFACTS_UNAVAILABLE",
        )
    try:
        client = _read_client(client, client_factory)
    except ImportError:
        return unavailable_candidate_prerequisites(
            codes, coursework, catalog_year=year, reason="PREREQUISITE_DEPENDENCIES_UNAVAILABLE",
        )
    except (OSError, ValueError, RuntimeError):
        # Client factory errors are deliberately sanitized. Never serialize SDK
        # exception messages or environment values into reports or console output.
        return unavailable_candidate_prerequisites(
            codes, coursework, catalog_year=year, reason="PREREQUISITE_CLIENT_UNAVAILABLE",
        )
    return evaluate_candidate_prerequisites(
        client, codes, coursework, catalog_year=year,
        source_catalog_csv=catalog, source_prerequisite_csv=prerequisites,
    )


def fetch_core_ii_candidates(
    handoff: dict[str, Any], progress_evidence: dict[str, Any], *, client: Any = None,
    client_factory: Callable[[], Any] | None = None, critical_thinking_candidates: bool = False,
) -> dict[str, Any]:
    """Run the standalone provider only for supported, confirmed unfinished categories.

    Preflight uses the provider's existing progress gates with an empty catalog
    response. Only a missing catalog for an otherwise actionable requirement
    triggers SELECTs; no independent copy of its academic-policy rules is added.
    """
    from backend.services.flexible_candidates import load_attribute_candidates, build_flexible_candidates

    year = handoff["degree"]["catalog_year"]
    loaded = load_attribute_candidates(None, [], catalog_year=year)
    preflight = build_flexible_candidates(handoff, loaded, critical_thinking_candidates=critical_thinking_candidates)
    needed = {r["requirement_id"] for r in preflight["requirements"]
              if r["reasons"] == ["MISSING_OR_MISMATCHED_CATALOG_DATA"]}
    names = sorted({name for r in handoff["requirements"] if r["rule_id"] in needed
                    for name in r["attribute_filter"]["attribute_names"]})
    if names:
        try:
            source = (_SOURCE_DIR / "catalog_attributes.csv").read_bytes()
        except OSError:
            loaded.update(retrieval_status="UNAVAILABLE", source_verification="SOURCE_ARTIFACT_UNAVAILABLE",
                          issues=[{"code": "ATTRIBUTE_SOURCE_ARTIFACT_UNAVAILABLE"}])
        else:
            try:
                client = _read_client(client, client_factory)
            except ImportError:
                loaded.update(retrieval_status="UNAVAILABLE", issues=[{"code": "ATTRIBUTE_DEPENDENCIES_UNAVAILABLE"}])
            except (OSError, ValueError, RuntimeError):
                loaded.update(retrieval_status="UNAVAILABLE", issues=[{"code": "ATTRIBUTE_CLIENT_UNAVAILABLE"}])
            else:
                loaded = load_attribute_candidates(client, names, catalog_year=year,
                                                   source_catalog_attributes_csv=source)
    result = build_flexible_candidates(handoff, loaded, critical_thinking_candidates=critical_thinking_candidates)
    result["completed_course_attribute_evidence"] = deepcopy(progress_evidence)
    states = {r["generation_status"] for r in result["requirements"]}
    if progress_evidence["status"] == "UNAVAILABLE" or loaded["retrieval_status"] == "UNAVAILABLE":
        status = "UNAVAILABLE"
    elif states & {"INSUFFICIENT_EVIDENCE", "INCOMPLETE", "PARTIAL"}:
        status = "PARTIAL"
    elif "GENERATED" in states:
        status = "COMPLETED"
    else:
        status = "NOT_NEEDED"
    result["generation_status"] = status
    return result


class DegreePlanResult(NamedTuple):
    preview: dict[str, Any]
    handoff: dict[str, Any] | None


def normalize_reported_credits(items: Iterable[tuple[str, float | str]]) -> dict[str, float]:
    """Check each entry before indexing it so duplicate course aliases remain visible."""
    credits = {}
    for raw_code, raw_amount in items:
        code = canonical_course_code(raw_code)
        if isinstance(raw_amount, bool) or not isinstance(raw_amount, (str, float, int)):
            raise PreviewError(f"Invalid reported credit amount for {code}")
        try:
            amount = float(raw_amount)
        except (OverflowError, ValueError) as error:
            raise PreviewError(f"Invalid reported credit amount for {code}") from error
        if not 0 < amount < float("inf"):
            raise PreviewError(f"Reported credits for {code} must be a finite positive number")
        if code in credits:
            raise PreviewError(f"Duplicate reported credits for {code}")
        credits[code] = amount
    return credits


def run_degree_plan(
    draft: dict[str, Any],
    completed_courses: list[str | dict[str, Any]],
    *,
    reported_credits: Mapping[str, float] | Iterable[tuple[str, float | str]] | None = None,
    core_ii_listing: dict[str, Any] | None = None,
    supabase_attributes: bool = False,
    evaluate_prerequisites: bool = False,
    core_ii_candidates: bool = False,
    critical_thinking_candidates: bool = False,
    include_handoff: bool = True,
    client: Any = None,
    client_factory: Callable[[], Any] | None = None,
) -> DegreePlanResult:
    """Evaluate a resolved draft against student-reported evidence.

    The default returns the legacy preview and v1 handoff without database access.
    Prerequisites enable v2.0. Core II implies both attribute and prerequisite
    lookups and enables v2.1. Critical Thinking extends Core II in internal v2.2.
    All lookups share the supplied Supabase client or
    one created on demand for this evaluation. Reviewed source CSVs are read only as needed.
    A preview-only caller can skip the v1 projection with include_handoff=False.
    """
    coursework = normalize_coursework(completed_courses)
    completed = coursework_codes(coursework)
    year = draft.get("catalog_year")
    flexible_requested = core_ii_candidates or critical_thinking_candidates
    enrich = evaluate_prerequisites or flexible_requested
    if enrich:
        validate_candidate_evidence([], coursework, catalog_year=year)

    credit_entries = reported_credits.items() if isinstance(reported_credits, Mapping) else reported_credits
    credits = normalize_reported_credits(credit_entries) if credit_entries is not None else {}

    evidence = None
    get_client = _PlanningClient(client, client_factory or supabase_service.create_supabase_client).get
    progress_evidence = {"status": "NOT_REQUESTED", "issues": []}
    if supabase_attributes or flexible_requested:
        try:
            evidence = fetch_supabase_attribute_evidence(completed, year, credits, client_factory=get_client)
            progress_evidence["status"] = "AVAILABLE"
        except (AttributeEvidenceUnavailable, OSError):
            if not flexible_requested:
                raise
            progress_evidence = {
                "status": "UNAVAILABLE", "issues": [{"code": "COMPLETED_COURSE_ATTRIBUTES_UNAVAILABLE"}],
            }
    elif credits:
        raise PreviewError("--reported-credits requires --supabase-attributes")

    preview = evaluate_preview(draft, completed, core_ii_listing, evidence)
    if not include_handoff and not enrich:
        return DegreePlanResult(preview, None)
    handoff = build_planning_handoff(preview)
    if enrich:
        original_warning_count = len(handoff["warnings"])
        flexible = None
        codes = None  # The eligibility provider defaults to the explicit/choice pool.
        if flexible_requested:
            flexible = fetch_core_ii_candidates(handoff, progress_evidence, client_factory=get_client,
                                                critical_thinking_candidates=critical_thinking_candidates)
            codes = combined_candidate_codes(handoff, flexible)
        results = fetch_planning_eligibility(handoff, coursework, candidate_course_codes=codes, client_factory=get_client)
        handoff = apply_prerequisite_results(handoff, results, flexible_candidates=flexible)
        if flexible is not None:
            preview["flexible_candidates"] = deepcopy(handoff["flexible_candidates"])
        preview["course_eligibility"] = deepcopy(handoff["course_eligibility"])
        preview["warnings"].extend(handoff["warnings"][original_warning_count:])
    return DegreePlanResult(preview, handoff)
