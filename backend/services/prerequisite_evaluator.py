"""Pure evaluation of reported coursework, NOT registration eligibility.

``evaluate_course_rules`` consumes the PREREQUISITE_PIPELINE.md read contract:
    {course_code, catalog_year, data_complete: True, source_url?, rules: [
        {rule_id?, rule_type, machine_checkable, parse_status, review_flags,
         clauses: [[{course_code, minimum_grade, concurrent_allowed}]], ...}
    ]}

The future loader must resolve IDs to exact catalog course codes, include ALL
rule types, preserve readiness/review flags, and explicitly assert data_complete
only after an unfiltered, complete read. Empty rules do not prove no prerequisites.
Missing/undated/mismatched data and unresolved rules remain UNKNOWN. This module
does not read a database, parse raw prerequisite prose, or approve equivalencies.
Do not pass the parser's legacy top-level dnf_clauses as an enrollment rule.

Coursework is a list of codes and/or {course_code, grade?} records. The optional
equivalency_verified=False marks evidence requiring an equivalency decision.
Codes are exact identities (case/spacing normalized); CS110 is accepted for audit
compatibility, but suffixes/hyphens are never removed. Identical records collapse;
different grades, including known versus missing, remain UNKNOWN for that course.
No latest-attempt, highest-grade, or grade-replacement policy is assumed.

Only A/B/C/D/F comparisons are supported. Plus/minus, P/S, transfer marks,
numeric strings and other grades are UNKNOWN, even without a grade threshold.
No reviewed plus/minus scale exists in this repository. Missing grades are OK
only when the rule has no minimum. F contradicts reported successful completion.
Invalid input shapes raise EvaluationInputError; unsupported grade strings are
evidence gaps, not input errors. Malformed rule data returns UNKNOWN diagnostics.

concurrent_courses=None means schedule evidence is unavailable; [] explicitly
reports no concurrent courses. A nonempty list asserts proposed enrollment in
the SAME term as the target, not completion or a future grade. Concurrent credit
never waives a minimum grade. COREQUISITE requires a normalized timing_policy of
CONCURRENT_ONLY or COMPLETED_OR_CONCURRENT, supplied only after policy review.
The current pipeline's unresolved corequisites cannot bypass readiness checks.

AND: UNMET dominates UNKNOWN; OR: MET dominates UNKNOWN. All applicable rules
are ANDed, retaining every rule's diagnostics. Missing/invalid rule-set context
overrides the aggregate to UNKNOWN. Group status is None when no rule of that
type was supplied (absence is not a MET result). Diagnostic unions are evidence
from ALL branches, not a prescription to complete every alternative.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from backend.scripts.parse_prerequisites import canonical_code, PrerequisiteParseError

MET = "MET"
UNMET = "UNMET"
UNKNOWN = "UNKNOWN"
_GRADES = {"F": 0, "D": 1, "C": 2, "B": 3, "A": 4}
_TYPES = ("PREREQUISITE", "COREQUISITE")
_PROVENANCE = ("rule_id", "course_id", "source_url", "raw_text", "content_sha256")
_ATOM_FIELDS = {
    "course_code", "minimum_grade", "concurrent_allowed", "required_course_id",
    "rule_course_id", "equivalency_verified",
}


class EvaluationInputError(ValueError):
    """The caller supplied malformed student evidence or request context."""


def _code(value: Any) -> str:
    if not isinstance(value, str):
        raise EvaluationInputError("course_code must be a string")
    text = " ".join(value.upper().split())
    # The audit accepts compact codes; use the parser's broader catalog identity
    # validator after inserting the separator for unambiguous letter subjects.
    compact = re.fullmatch(r"([A-Z&]{2,8})(\d{2,4}[A-Z0-9]*(?:-[A-Z0-9]+)?)", text)
    if compact:
        text = f"{compact[1]} {compact[2]}"
    try:
        return canonical_code(text)
    except PrerequisiteParseError as error:
        raise EvaluationInputError(str(error)) from error


def _coursework(records: Any) -> dict[str, set[tuple[str | None, bool]]]:
    if not isinstance(records, list):
        raise EvaluationInputError("completed_courses must be a list")
    courses: dict[str, set[tuple[str | None, bool]]] = {}
    for record in records:
        if isinstance(record, str):
            code, grade, equivalent = _code(record), None, True
        elif isinstance(record, dict):
            if set(record) - {"course_code", "grade", "equivalency_verified"}:
                raise EvaluationInputError("Unsupported coursework fields; normalize evidence explicitly")
            code, grade = _code(record.get("course_code")), record.get("grade")
            if grade is not None and not isinstance(grade, str):
                raise EvaluationInputError("grade must be a string or null")
            grade = grade.strip().upper() if grade is not None else None
            equivalent = record.get("equivalency_verified", True)
            if not isinstance(equivalent, bool):
                raise EvaluationInputError("equivalency_verified must be boolean")
        else:
            raise EvaluationInputError("Each coursework entry must be a code or record")
        courses.setdefault(code, set()).add((grade, equivalent))
    return courses


def _and(statuses: list[str]) -> str:
    if UNMET in statuses:
        return UNMET
    return UNKNOWN if not statuses or UNKNOWN in statuses else MET


def _or(statuses: list[str]) -> str:
    if MET in statuses:
        return MET
    return UNKNOWN if not statuses or UNKNOWN in statuses else UNMET


def _completion(code: str, minimum: str | None, courses: dict) -> tuple[str, str]:
    attempts = courses.get(code)
    if not attempts:
        return UNMET, "COURSE_NOT_REPORTED_COMPLETED"
    if len(attempts) > 1:
        return UNKNOWN, "REPEATED_COURSE_EVIDENCE_CONFLICT"
    grade, equivalent = next(iter(attempts))
    if not equivalent:
        return UNKNOWN, "EQUIVALENCY_UNVERIFIED"
    if grade is not None and grade not in _GRADES:
        return UNKNOWN, "UNSUPPORTED_REPORTED_GRADE"
    if grade == "F":
        return UNMET, "FAILING_GRADE"
    if minimum is not None and grade is None:
        return UNKNOWN, "MISSING_REPORTED_GRADE"
    if minimum is not None and _GRADES[grade] < _GRADES[minimum]:
        return UNMET, "GRADE_BELOW_MINIMUM"
    return MET, "REPORTED_COMPLETION_MEETS_CONDITION"


def _concurrent(code: str, minimum: str | None, concurrent: set[str] | None) -> tuple[str, str]:
    if concurrent is None:
        return UNKNOWN, "CONCURRENT_ENROLLMENT_EVIDENCE_MISSING"
    if code not in concurrent:
        return UNMET, "COURSE_NOT_REPORTED_CONCURRENT"
    if minimum is not None:
        return UNKNOWN, "CONCURRENT_GRADE_NOT_ESTABLISHED"
    return MET, "REPORTED_SAME_TERM_ENROLLMENT"


def _atom(atom: dict, rule: dict, courses: dict, concurrent: set[str] | None) -> dict:
    code = _code(atom["course_code"])
    minimum = atom["minimum_grade"]
    minimum = minimum.strip().upper() if isinstance(minimum, str) else minimum
    result = {
        "course_code": code, "minimum_grade": minimum,
        "concurrent_allowed": atom["concurrent_allowed"],
        "reported_grades": sorted({g for g, _ in courses.get(code, set()) if g is not None}),
        "reported_without_grade": any(g is None for g, _ in courses.get(code, set())),
        "evidence": [],
    }
    for key in ("required_course_id", "rule_course_id"):
        if key in atom:
            result[key] = deepcopy(atom[key])
    if atom.get("equivalency_verified") is False:
        result.update(status=UNKNOWN, reasons=["EQUIVALENCY_UNVERIFIED"])
        return result
    if minimum is not None and minimum not in _GRADES:
        result.update(status=UNKNOWN, reasons=["UNSUPPORTED_MINIMUM_GRADE"])
        return result
    coreq = rule["rule_type"] == "COREQUISITE"
    if not coreq or rule["timing_policy"] == "COMPLETED_OR_CONCURRENT":
        status, reason = _completion(code, minimum, courses)
        result["evidence"].append({"kind": "COMPLETED", "status": status, "reason": reason})
    if coreq or atom["concurrent_allowed"]:
        status, reason = _concurrent(code, minimum, concurrent)
        result["evidence"].append({"kind": "CONCURRENT", "status": status, "reason": reason})
    result["status"] = _or([e["status"] for e in result["evidence"]])
    result["reasons"] = [e["reason"] for e in result["evidence"]]
    return result


def _rule_issues(rule: dict, catalog_year: str, target: str) -> list[str]:
    issues = []
    if rule.get("rule_type") not in _TYPES:
        issues.append("UNSUPPORTED_RULE_TYPE")
    if rule.get("machine_checkable") is not True:
        issues.append("RULE_NOT_MACHINE_CHECKABLE")
    if rule.get("parse_status") != "READY_FOR_SEED_REVIEW":
        issues.append("RULE_NOT_READY")
    if rule.get("review_flags") != []:
        issues.append("RULE_REVIEW_FLAGS_OR_MISSING_REVIEW_STATE")
    if "catalog_year" in rule and rule["catalog_year"] != catalog_year:
        issues.append("RULE_CATALOG_YEAR_MISMATCH")
    if "catalog_year_id" in rule and rule["catalog_year_id"] is None:
        issues.append("UNDATED_RULE")
    if "course_code" in rule:
        try:
            if _code(rule["course_code"]) != target:
                issues.append("RULE_TARGET_MISMATCH")
        except EvaluationInputError:
            issues.append("UNMAPPED_RULE_TARGET")
    if rule.get("condition_text") or rule.get("unsupported_conditions"):
        issues.append("UNSUPPORTED_RULE_CONDITIONS")
    if rule.get("rule_type") == "COREQUISITE" and rule.get("timing_policy") not in (
        "CONCURRENT_ONLY", "COMPLETED_OR_CONCURRENT",
    ):
        issues.append("COREQUISITE_TIMING_UNKNOWN")
    clauses = rule.get("clauses")
    if not isinstance(clauses, list) or not clauses:
        return issues + ["MISSING_OR_INVALID_CLAUSES"]
    for clause in clauses:
        if not isinstance(clause, list) or not clause:
            issues.append("EMPTY_OR_INVALID_CLAUSE")
            continue
        seen = set()
        for atom in clause:
            if not isinstance(atom, dict) or set(atom) - _ATOM_FIELDS:
                issues.append("UNSUPPORTED_OR_INVALID_ATOM")
                continue
            try:
                code = _code(atom.get("course_code"))
            except EvaluationInputError:
                issues.append("UNMAPPED_REQUIRED_COURSE")
                continue
            if code in seen:
                issues.append("DUPLICATE_COURSE_IN_CLAUSE")
            seen.add(code)
            if "minimum_grade" not in atom or (
                atom["minimum_grade"] is not None and not isinstance(atom["minimum_grade"], str)
            ):
                issues.append("INVALID_MINIMUM_GRADE_FIELD")
            if not isinstance(atom.get("concurrent_allowed"), bool):
                issues.append("INVALID_CONCURRENCY_FIELD")
            if "equivalency_verified" in atom and not isinstance(atom["equivalency_verified"], bool):
                issues.append("INVALID_EQUIVALENCY_FIELD")
    return list(dict.fromkeys(issues))


def _diagnostics(clauses: list[dict]) -> dict:
    atoms = [a for c in clauses for a in c["conditions"]]
    grade_reasons = {
        "MISSING_REPORTED_GRADE", "UNSUPPORTED_REPORTED_GRADE", "UNSUPPORTED_MINIMUM_GRADE",
        "REPEATED_COURSE_EVIDENCE_CONFLICT", "CONCURRENT_GRADE_NOT_ESTABLISHED",
    }
    return {
        "missing_courses": sorted({a["course_code"] for a in atoms
                                   if a["status"] != MET and any(r in a["reasons"] for r in (
                                       "COURSE_NOT_REPORTED_COMPLETED", "COURSE_NOT_REPORTED_CONCURRENT"))}),
        "insufficient_grade_evidence": [
            {"course_code": a["course_code"], "minimum_grade": a["minimum_grade"],
             "reasons": [r for r in a["reasons"] if r in grade_reasons]}
            for a in atoms if a["status"] == UNKNOWN and grade_reasons.intersection(a["reasons"])
        ],
    }


def _evaluate_rule(rule: Any, index: int, year: str, target: str, courses: dict,
                   concurrent: set[str] | None) -> dict:
    result: dict[str, Any] = {
        "rule_index": index, "rule_type": None, "status": UNKNOWN, "reasons": [],
        "clauses": [], "missing_courses": [], "insufficient_grade_evidence": [],
    }
    if not isinstance(rule, dict):
        result["reasons"] = ["INVALID_RULE"]
        return result
    result.update({k: deepcopy(rule[k]) for k in (*_PROVENANCE, "rule_type", "review_flags") if k in rule})
    result["catalog_year"] = deepcopy(rule.get("catalog_year", year))
    issues = _rule_issues(rule, year, target)
    if issues:
        result["reasons"] = issues
        return result
    for number, clause in enumerate(rule["clauses"], 1):
        conditions = [_atom(atom, rule, courses, concurrent) for atom in clause]
        result["clauses"].append({
            "clause_number": number, "status": _and([a["status"] for a in conditions]),
            "conditions": conditions,
        })
    result["status"] = _or([c["status"] for c in result["clauses"]])
    result["reasons"] = [{MET: "DNF_ALTERNATIVE_MET", UNMET: "ALL_DNF_ALTERNATIVES_UNMET",
                          UNKNOWN: "DNF_EVIDENCE_INCOMPLETE"}[result["status"]]]
    result.update(_diagnostics(result["clauses"]))
    return result


def evaluate_course_rules(
    course_code: str,
    completed_courses: list[str | dict[str, Any]],
    rule_data: dict[str, Any] | None,
    *,
    catalog_year: str,
    concurrent_courses: list[str] | None = None,
) -> dict[str, Any]:
    """Return JSON-compatible MET/UNMET/UNKNOWN evidence without side effects.

    See the module contract for data completeness, timing and repeat policies.
    The result's per-type summaries and individual rules must be retained by a
    future consumer; a satisfied prerequisite never clears an unknown corequisite.
    """
    target = _code(course_code)
    if not isinstance(catalog_year, str) or not catalog_year.strip():
        raise EvaluationInputError("catalog_year must be a nonempty catalog label")
    year = catalog_year.strip()
    courses = _coursework(completed_courses)
    if concurrent_courses is not None and not isinstance(concurrent_courses, list):
        raise EvaluationInputError("concurrent_courses must be a list of codes or null")
    concurrent = None if concurrent_courses is None else {_code(c) for c in concurrent_courses}
    result: dict[str, Any] = {
        "course_code": target, "catalog_year": year, "status": UNKNOWN,
        "basis": "STUDENT_REPORTED_EVIDENCE", "registration_eligibility_determined": False,
        "reasons": [], "rules": [], "rule_groups": {},
        "missing_courses": [], "insufficient_grade_evidence": [],
    }
    context_issues = []
    rules: list = []
    if not isinstance(rule_data, dict):
        context_issues.append("MISSING_OR_INVALID_RULE_DATA")
    else:
        try:
            if _code(rule_data.get("course_code")) != target:
                context_issues.append("TARGET_COURSE_MISMATCH")
        except EvaluationInputError:
            context_issues.append("UNMAPPED_TARGET_COURSE")
        if rule_data.get("catalog_year") != year:
            context_issues.append("MISSING_OR_MISMATCHED_CATALOG_YEAR")
        if rule_data.get("data_complete") is not True:
            context_issues.append("RULE_DATA_COMPLETENESS_UNVERIFIED")
        if "source_url" in rule_data:
            result["source_url"] = deepcopy(rule_data["source_url"])
        if not isinstance(rule_data.get("rules"), list) or not rule_data["rules"]:
            context_issues.append("NO_RULES_OR_INVALID_RULE_LIST")
        else:
            rules = rule_data["rules"]
    # Never evaluate stale/partial data into apparently usable per-rule results.
    if context_issues:
        result["reasons"] = context_issues
    else:
        result["rules"] = [_evaluate_rule(r, i, year, target, courses, concurrent)
                           for i, r in enumerate(rules, 1)]
        result["status"] = _and([r["status"] for r in result["rules"]])
        result["reasons"] = [{MET: "ALL_SUPPLIED_RULES_MET", UNMET: "REQUIRED_RULE_UNMET",
                              UNKNOWN: "REQUIRED_RULE_UNKNOWN"}[result["status"]]]
        result["missing_courses"] = sorted({c for r in result["rules"] for c in r["missing_courses"]})
        result["insufficient_grade_evidence"] = [
            {"rule_index": r["rule_index"], "rule_id": r.get("rule_id"), **e}
            for r in result["rules"] for e in r["insufficient_grade_evidence"]
        ]
    for kind in _TYPES:
        matches = [r for r in result["rules"] if r["rule_type"] == kind]
        result["rule_groups"][kind] = {
            "status": _and([r["status"] for r in matches]) if matches else UNKNOWN if context_issues else None,
            "rule_count": len(matches),
        }
    return result
