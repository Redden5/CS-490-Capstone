"""Batch prerequisite evidence for any catalog course candidate provider.

No environment, file access, client creation, degree-progress decisions or writes.
Explicit degree options and future catalog-year attribute candidates use the same
code-list interface. The caller retains each candidate's requirement membership.
Diagnostic missing_courses are unions of alternatives, never scheduling demands.
"""

from copy import deepcopy
import re
from typing import Any

from backend.services.prerequisite_evaluator import (
    EvaluationInputError, _code, _coursework, evaluate_course_rules,
)
from backend.services.prerequisite_loader import PrerequisiteLoadError, load_prerequisite_rules


def validate_candidate_evidence(
    candidate_course_codes, completed_courses, *, catalog_year, concurrent_courses=None,
) -> list[str]:
    """Validate even empty/unavailable requests before any I/O; preserve attempts.

    Reuse Stage 1's validators (as the loader already does) rather than create a
    competing grade, repetition or catalog-identity policy. No evidence is dropped.
    """
    if not isinstance(candidate_course_codes, list):
        raise EvaluationInputError("candidate_course_codes must be a list")
    codes = sorted({_code(c) for c in candidate_course_codes})
    _coursework(completed_courses)
    if not isinstance(catalog_year, str) or not re.fullmatch(r"\d{4}-\d{4}", catalog_year):
        raise EvaluationInputError("catalog_year must be a YYYY-YYYY label")
    if concurrent_courses is not None:
        if not isinstance(concurrent_courses, list):
            raise EvaluationInputError("concurrent_courses must be a list or null")
        for code in concurrent_courses:
            _code(code)
    return codes


def _block(year, status, courses, *, source="NOT_CHECKED", retrieval="NOT_REQUESTED", issues=None):
    return {
        "evaluation_status": status,
        "default_status": "UNKNOWN",
        "catalog_year": year,
        "basis": "STUDENT_REPORTED_EVIDENCE",
        "registration_eligibility_determined": False,
        "source_verification": source,
        "retrieval_status": retrieval,
        "issues": list(issues or []),
        "diagnostic_scope": "ALL_ALTERNATIVES_NOT_MANDATORY_COURSEWORK",
        "grades_used_for_degree_progress": False,
        "courses": courses,
        "summary": {state: sum(c["status"] == state for c in courses)
                    for state in ("MET", "UNMET", "UNKNOWN")},
    }


def unavailable_candidate_prerequisites(
    candidate_course_codes, completed_courses, *, catalog_year, reason,
    concurrent_courses=None, table=None,
) -> dict[str, Any]:
    """Represent an expected availability failure; reason/table are safe codes.

    Callers must never pass exception messages, credentials or URLs here.
    """
    codes = validate_candidate_evidence(candidate_course_codes, completed_courses,
                                        catalog_year=catalog_year, concurrent_courses=concurrent_courses)
    if not isinstance(reason, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", reason):
        raise ValueError("Availability reason must be a diagnostic code")
    if table is not None and table not in {"course", "catalogyear", "courserule", "ruleclause", "rulecourse"}:
        raise ValueError("Unexpected prerequisite table")
    issue = {"code": reason}
    if table is not None:
        issue["table"] = table
    courses = []
    for code in codes:
        result = evaluate_course_rules(code, completed_courses, None, catalog_year=catalog_year,
                                       concurrent_courses=concurrent_courses)
        result.update(reason=reason, loading={
            "data_complete": False, "load_status": "UNAVAILABLE", "coverage_status": "UNKNOWN",
            "evaluability": "UNKNOWN", "issues": [reason], "excluded_legacy_rule_ids": [],
            "clause_provenance": [], "rule_provenance": [],
        })
        courses.append(result)
    return _block(catalog_year, "UNAVAILABLE" if codes else "NO_CANDIDATES", courses,
                  retrieval="FAILED" if table is not None and codes else "NOT_REQUESTED",
                  issues=[issue] if codes else [])


def evaluate_candidate_prerequisites(
    client,
    candidate_course_codes: list[str],
    completed_courses: list[str | dict[str, Any]],
    *,
    catalog_year: str,
    source_catalog_csv: bytes | None = None,
    source_prerequisite_csv: bytes | None = None,
    concurrent_courses: list[str] | None = None,
) -> dict[str, Any]:
    """Load once for all candidates; retain completeness separately from truth.

    Only sanitized loader availability failures degrade to UNKNOWN. Malformed
    caller evidence and unexpected programming errors propagate to the caller.
    No rule completeness assertion or alternative-selection policy is accepted.
    """
    codes = validate_candidate_evidence(candidate_course_codes, completed_courses,
                                        catalog_year=catalog_year, concurrent_courses=concurrent_courses)
    if not codes:
        return _block(catalog_year, "NO_CANDIDATES", [])
    try:
        loaded = load_prerequisite_rules(
            client, codes, catalog_year=catalog_year,
            source_catalog_csv=source_catalog_csv, source_prerequisite_csv=source_prerequisite_csv,
        )
    except PrerequisiteLoadError as error:
        return unavailable_candidate_prerequisites(
            codes, completed_courses, catalog_year=catalog_year, reason=error.code,
            table=error.table, concurrent_courses=concurrent_courses,
        )
    courses = []
    for code in codes:
        item = loaded["courses"][code]
        data = item["rule_data"]
        result = evaluate_course_rules(code, completed_courses, data, catalog_year=catalog_year,
                                       concurrent_courses=concurrent_courses)
        result["reason"] = "PREREQUISITE_RULES_" + result["status"]
        result["loading"] = {
            **deepcopy({k: item[k] for k in (
                "load_status", "coverage_status", "evaluability", "issues",
                "excluded_legacy_rule_ids", "clause_provenance",
            )}),
            "data_complete": data["data_complete"],
            "catalog_year_id": data.get("catalog_year_id"),
            # Keep readiness/raw provenance even when evaluation returns early.
            "rule_provenance": [deepcopy({k: v for k, v in rule.items() if k != "clauses"})
                                for rule in data["rules"]],
        }
        courses.append(result)
    status = "COMPLETED" if all(c["loading"]["data_complete"] for c in courses) else "PARTIAL"
    return _block(catalog_year, status, courses, source=loaded["source_verification"],
                  retrieval=loaded["retrieval_status"])
