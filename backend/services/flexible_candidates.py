"""Year-scoped catalog membership for Core II and opt-in CS Core I Critical Thinking.

load_attribute_candidates(client, attribute_names, *, catalog_year,
                          source_catalog_attributes_csv=None) uses SELECT only.
The caller owns credentials and file I/O. Pass the bytes of the reviewed
backend/scripts/catalog_attributes.csv to reconcile memberships. A pinned hash
identifies that snapshot; arbitrary replacement bytes are not a coverage witness.
Coverage is relative to that snapshot, NOT unrestricted RLS visibility, an atomic
snapshot or current catalog completeness. Two paginated graph reads detect drift.
Missing/changed source bytes allow positive year-scoped database evidence, but
never establish exhaustive coverage. Unexpected source memberships and unstable
reads are retained as evidence and withheld from ordinary candidates.

Return: {catalog_year, catalog_year_id, requested_attributes, source_verification,
         retrieval_status, issues, attributes: {name: {attribute_id, courses,
         coverage_complete, coverage_status, issues, missing_source_memberships,
         unexpected_source_memberships}}}. Query/protocol failures return an
UNAVAILABLE result with no partial courses and sanitized code/table diagnostics.
Invalid caller arguments raise ValueError. No writes, RPC, SDK or env imports.

build_flexible_candidates(handoff, attribute_candidates) is pure. It accepts the
existing finalized v1/v2 handoff (use build_planning_handoff for audit previews).
It returns separate requirement candidates, withheld evidence, skipped reasons
and a deduplicated candidate_course_codes list for later prerequisite evaluation.
Only supported, policy-clear requirements with consistent, known positive credit
shortfalls qualify. It does not recalculate degree progress, choose alternatives,
allocate shared credits, test prerequisites, enumerate sections or change schemas.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import csv
from datetime import datetime
import hashlib
import io
import math
import re
from typing import Any
from urllib.parse import urlsplit

from backend.scripts.resolve_requirements import CATALOG_ATTRIBUTE_LABELS, normalize_heading
from backend.services.prerequisite_evaluator import _code


SUPPORTED_ATTRIBUTES = ("Core II Fine Arts", "Core II Humanities", "Core II Social Sciences")
CRITICAL_THINKING = "Critical Thinking"
CT_AND_CORE_II_ATTRIBUTES = (*SUPPORTED_ATTRIBUTES, CRITICAL_THINKING)
_SOURCE_YEAR = "2026-2027"
# Reviewed dated export already present in this repository; not a caller override.
_SOURCE_SHA256 = "d879747438c30a0b9604157e714052a646370617af363973e282ccf8d5dc0faa"
_COLUMNS = {
    "catalogyear": ("catalog_year_id", "catalog_label"),
    "attribute": ("attribute_id", "attribute_name"),
    "courseattribute": ("course_id", "attribute_id", "source_url", "observed_at", "catalog_year_id"),
    "course": ("course_id", "course_code", "course_title", "min_credit_hours", "max_credit_hours",
               "active_status", "source_url"),
}
_KEYS = {table: columns[:1] for table, columns in _COLUMNS.items()}
_KEYS["courseattribute"] = ("course_id", "attribute_id", "source_url")


def _id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _year(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{4}", value):
        raise ValueError("catalog_year must be a YYYY-YYYY label")
    return value


def _url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def _timestamp(value):
    try:
        return isinstance(value, str) and datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        return False


class _ReadError(Exception):
    def __init__(self, code, table):
        self.code, self.table = code, table
        super().__init__(code)


def _rows(client, table, page_size, *, equals=None, within=None):
    rows, total, previous = [], None, None
    keys = _KEYS[table]
    while True:
        try:
            query = client.table(table).select(",".join(_COLUMNS[table]), count="exact")
            for key, value in (equals or {}).items():
                query = query.eq(key, value)
            for key, values in (within or {}).items():
                query = query.in_(key, values)
            for key in keys:
                query = query.order(key)
            response = query.range(len(rows), len(rows) + page_size - 1).execute()
        except Exception:
            raise _ReadError("QUERY_ERROR", table) from None
        batch, count = getattr(response, "data", None), getattr(response, "count", None)
        if not isinstance(batch, list) or not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise _ReadError("INVALID_RESPONSE_OR_MISSING_COUNT", table)
        if total is None:
            total = count
        if total != count:
            raise _ReadError("COUNT_CHANGED_DURING_READ", table)
        if len(batch) > page_size or len(rows) + len(batch) > total or (not batch and len(rows) < total):
            raise _ReadError("INCOMPLETE_PAGE", table)
        for row in batch:
            if not isinstance(row, dict) or any(k not in row for k in _COLUMNS[table]):
                raise _ReadError("MALFORMED_ROW", table)
            if any(not _id(row[k]) for k in keys if k.endswith("_id")) or (
                table == "courseattribute" and (
                    not isinstance(row["source_url"], str) or not _id(row["catalog_year_id"])
                )
            ):
                raise _ReadError("INVALID_ROW_IDENTITY", table)
            key = tuple(row[k] for k in keys)
            if previous is not None and key <= previous:
                raise _ReadError("DUPLICATE_OR_UNORDERED_ROW", table)
            if any(row[k] != v for k, v in (equals or {}).items()) or any(
                row[k] not in values for k, values in (within or {}).items()
            ):
                raise _ReadError("FILTER_MISMATCH", table)
            previous = key
            rows.append(deepcopy(row))
        if len(rows) == total:
            return rows


def _batch(client, table, key, values, batch_size, page_size, **filters):
    values, rows = sorted(set(values)), []
    for start in range(0, len(values), batch_size):
        rows.extend(_rows(client, table, page_size, within={key: values[start:start + batch_size]}, **filters))
    keys = [tuple(r[k] for k in _KEYS[table]) for r in rows]
    if len(keys) != len(set(keys)):
        raise _ReadError("DUPLICATE_ROW_ACROSS_BATCHES", table)
    return sorted(rows, key=lambda r: tuple(r[k] for k in _KEYS[table]))


def _graph(client, names, year, batch_size, page_size):
    years = _rows(client, "catalogyear", page_size, equals={"catalog_label": year})
    if len(years) != 1:
        return {"years": years, "attributes": [], "associations": [], "courses": []}
    attributes = _batch(client, "attribute", "attribute_name", names, batch_size, page_size)
    associations = _batch(client, "courseattribute", "attribute_id", [a["attribute_id"] for a in attributes],
                          batch_size, page_size, equals={"catalog_year_id": years[0]["catalog_year_id"]})
    courses = _batch(client, "course", "course_id", [a["course_id"] for a in associations], batch_size, page_size)
    return dict(years=years, attributes=attributes, associations=associations, courses=courses)


def _source(data, year):
    if data is None:
        return "SOURCE_NOT_SUPPLIED", {}
    if year != _SOURCE_YEAR:
        return "SOURCE_YEAR_UNSUPPORTED", {}
    if hashlib.sha256(data).hexdigest() != _SOURCE_SHA256:
        return "SOURCE_HASH_MISMATCH", {}
    memberships = defaultdict(set)
    seen = set()
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        for row in reader:
            code, name, url = _code(row["course_code"]), row["attribute_name"], row["source_url"]
            key = (code, name, url)
            if (key in seen or row["catalog_year"] != year or not _url(url)
                    or not _timestamp(row["observed_at_utc"]) or not name):
                raise ValueError("Invalid source association")
            seen.add(key)
            memberships[name].add((code, url))
        if not seen:
            raise ValueError("Empty source")
    except (ValueError, KeyError, TypeError, csv.Error):
        return "SOURCE_INVALID", {}
    return "HASH_VERIFIED_SOURCE", dict(memberships)


def _empty_attribute():
    return {"attribute_id": None, "courses": [], "coverage_complete": False,
            "coverage_status": "UNKNOWN", "issues": [],
            "missing_source_memberships": [], "unexpected_source_memberships": []}


def load_attribute_candidates(
    client, attribute_names: list[str], *, catalog_year: str,
    source_catalog_attributes_csv: bytes | None = None, batch_size: int = 100, page_size: int = 500,
) -> dict[str, Any]:
    """Retrieve only explicitly requested, supported catalog attributes."""
    year = _year(catalog_year)
    if not isinstance(attribute_names, list) or any(
        not isinstance(n, str) or n not in CT_AND_CORE_II_ATTRIBUTES for n in attribute_names
    ):
        raise ValueError("attribute_names must be a list of supported exact catalog attributes")
    for size in (batch_size, page_size):
        if not _id(size) or size > 1000:
            raise ValueError("batch_size and page_size must be integers from 1 to 1000")
    if source_catalog_attributes_csv is not None and not isinstance(source_catalog_attributes_csv, bytes):
        raise ValueError("source_catalog_attributes_csv must be bytes or null")
    names = sorted(set(attribute_names))
    source_status, expected = _source(source_catalog_attributes_csv, year)
    result = {"catalog_year": year, "catalog_year_id": None, "requested_attributes": names,
              "source_verification": source_status, "retrieval_status": "NOT_REQUESTED", "issues": [],
              "attributes": {n: _empty_attribute() for n in names}}
    if not names:
        return result
    try:
        first = _graph(client, names, year, batch_size, page_size)
        second = _graph(client, names, year, batch_size, page_size)
    except _ReadError as error:
        result.update(retrieval_status="UNAVAILABLE", issues=[{"code": error.code, "table": error.table}])
        for item in result["attributes"].values():
            item["issues"] = ["RETRIEVAL_UNAVAILABLE"]
        return result
    stable = first == second
    result["retrieval_status"] = "VISIBLE_READS_AGREE_NOT_ATOMIC" if stable else "READ_CHANGED"
    year_id = first["years"][0]["catalog_year_id"] if len(first["years"]) == 1 else None
    result["catalog_year_id"] = year_id
    courses = {r["course_id"]: r for r in first["courses"]}
    code_counts = Counter()
    for row in courses.values():
        try:
            row["course_code"] = _code(row["course_code"])
            code_counts[row["course_code"]] += 1
        except ValueError:
            row["course_code"] = None
    for name, item in result["attributes"].items():
        issues = item["issues"]
        if not stable:
            issues.append("READ_CHANGED")
        if year_id is None:
            issues.append("CATALOG_YEAR_MISSING_OR_AMBIGUOUS_OR_HIDDEN")
        definitions = [a for a in first["attributes"] if a["attribute_name"] == name]
        if len(definitions) != 1:
            issues.append("ATTRIBUTE_MISSING_OR_AMBIGUOUS_OR_HIDDEN")
        else:
            item["attribute_id"] = definitions[0]["attribute_id"]
        associations = [a for a in first["associations"] if a["attribute_id"] == item["attribute_id"]]
        by_course, visible = {}, set()
        for association in associations:
            row = courses.get(association["course_id"])
            if row is None:
                issues.append("REFERENCED_COURSE_MISSING_OR_HIDDEN")
                continue
            code = row["course_code"]
            if code is None or code_counts[code] != 1:
                issues.append("INVALID_OR_AMBIGUOUS_COURSE_CODE")
                continue
            valid_provenance = _url(association["source_url"]) and _timestamp(association["observed_at"])
            if not valid_provenance:
                issues.append("INVALID_CLASSIFICATION_PROVENANCE")
            member = (code, association["source_url"])
            visible.add(member)
            course = by_course.setdefault(code, {
                **deepcopy(row), "catalog_year": year, "catalog_year_id": year_id,
                "matched_attributes": [name], "classification_sources": [],
                "membership_status": "YEAR_SCOPED_DATABASE_ASSOCIATION", "issues": [],
            })
            course["classification_sources"].append(deepcopy(association))
            if not valid_provenance:
                course["issues"].append("INVALID_CLASSIFICATION_PROVENANCE")
        for code, course in by_course.items():
            # Keep malformed REST numeric evidence JSON-compatible. Finite but
            # invalid ranges stay visible and are withheld by the pure builder.
            for field in ("min_credit_hours", "max_credit_hours"):
                value = course[field]
                if isinstance(value, (int, float)) and not _number(value):
                    course[field] = None
                    course["issues"].append("INVALID_NUMERIC_CREDIT_EVIDENCE")
            source_matches = [s for s in course["classification_sources"]
                              if (code, s["source_url"]) in expected.get(name, set())]
            if not stable:
                course["membership_status"] = "UNSTABLE_READ"
            elif source_status == "HASH_VERIFIED_SOURCE":
                course["membership_status"] = "MATCHES_REVIEWED_SOURCE" if source_matches else "NOT_IN_REVIEWED_SOURCE"
            course["issues"] = sorted(set(course["issues"]))
        item["courses"] = [by_course[c] for c in sorted(by_course)]
        if source_status != "HASH_VERIFIED_SOURCE":
            issues.append(source_status)
            item["coverage_status"] = "SOURCE_UNVERIFIED"
        else:
            wanted = expected.get(name, set())
            item["missing_source_memberships"] = [dict(course_code=c, source_url=u) for c, u in sorted(wanted - visible)]
            item["unexpected_source_memberships"] = [dict(course_code=c, source_url=u) for c, u in sorted(visible - wanted)]
            if name not in expected:
                issues.append("CATEGORY_NOT_IN_REVIEWED_SOURCE")
            if wanted != visible:
                issues.append("SOURCE_MEMBERSHIP_MISMATCH")
            item["coverage_complete"] = not issues
            item["coverage_status"] = "MATCHES_REVIEWED_SOURCE" if not issues else "INCOMPLETE_OR_DIFFERENT"
        item["issues"] = sorted(set(issues))
    return result


def _credit_fit(course, remaining):
    low, high = course["min_credit_hours"], course["max_credit_hours"]
    if not (_number(low) and _number(high) and 0 <= low <= high):
        return {"kind": "UNKNOWN", "status": "CREDIT_EVIDENCE_MISSING", "assumed_awarded_credits": None}
    if high == 0:
        status = "NO_CREDIT_CONTRIBUTION"
    elif high < remaining:
        status = "PARTIAL_CONTRIBUTION"
    elif low >= remaining:
        status = "COVERS_REMAINING_CREDITS"
    else:
        status = "DEPENDS_ON_SELECTED_CREDITS"
    return {"kind": "FIXED" if low == high else "VARIABLE", "status": status,
            "assumed_awarded_credits": None,
            "fixed_credit_estimate": low if low == high else None,
            "contribution_range": {"min": min(low, remaining), "max": min(high, remaining)}}


def _progress_issues(requirement, year, completed):
    attr, credit = requirement.get("attribute_filter") or {}, requirement.get("credit_progress") or {}
    if not isinstance(attr, dict) or not isinstance(credit, dict):
        return ["MALFORMED_REQUIREMENT_EVIDENCE"]
    issues = []
    if (requirement.get("candidate_progress_status") != "CANDIDATE_NOT_MET"
            or requirement.get("planning_disposition") != "CANDIDATE_COURSEWORK_REMAINING"
            or requirement.get("additional_coursework_indicated") is not True):
        issues.append("REMAINING_REQUIREMENT_NOT_ESTABLISHED")
    if requirement.get("remaining_policy_review_flags") != []:
        issues.append("ACADEMIC_POLICY_UNRESOLVED")
    if requirement.get("source_status") not in ("READY", "NEEDS_REVIEW"):
        issues.append("UNSUPPORTED_SOURCE_STATUS")
    if (attr.get("catalog_year_classification_lookup_applied") is not True or attr.get("catalog_year") != year):
        issues.append("CATALOG_YEAR_PROGRESS_EVIDENCE_MISSING")
    entered = attr.get("candidate_course_codes_entered")
    if not isinstance(entered, list) or any(not isinstance(c, str) or c not in completed for c in entered):
        issues.append("INVALID_COMPLETED_CLASSIFICATION_EVIDENCE")
    remaining = credit.get("additional_credits_indicated_candidate")
    minimum, known = credit.get("minimum_credits_interpreted"), credit.get("candidate_known_credit_subtotal")
    if (not (_number(remaining) and remaining > 0 and _number(minimum) and minimum > 0
             and _number(known) and known >= 0)
            or credit.get("candidate_courses_without_known_credits") != []):
        issues.append("KNOWN_POSITIVE_CREDIT_SHORTFALL_REQUIRED")
    elif not math.isclose(remaining, minimum - known, rel_tol=1e-9, abs_tol=1e-9):
        issues.append("INCONSISTENT_CREDIT_SHORTFALL")
    if _number(known) and known > 0 and not entered:
        issues.append("COMPLETED_CREDIT_EVIDENCE_MISSING")
    return issues


def build_flexible_candidates(handoff: dict[str, Any], attribute_candidates: dict[str, Any], *,
                              critical_thinking_candidates: bool = False) -> dict[str, Any]:
    """Build separate requirement candidates; never mutate or enrich the handoff.

    Candidates are requirement matches, NOT prerequisite-approved recommendations.
    Inactive, ambiguous, unverified-source-extra and unknown-credit evidence is
    retained in withheld_courses. Partial source coverage stays explicit even
    when individual memberships are usable. No maximum variable credit is awarded.
    """
    if not isinstance(handoff, dict) or handoff.get("schema_version") not in ("1.0.0", "2.0.0"):
        raise ValueError("Expected a finalized v1 or v2 planning handoff")
    degree = handoff.get("degree")
    if not isinstance(degree, dict):
        raise ValueError("Handoff requires degree metadata")
    year = _year(degree.get("catalog_year"))
    requirements, entered = handoff.get("requirements"), handoff.get("completed_course_codes_entered")
    if not isinstance(requirements, list) or not isinstance(entered, list):
        raise ValueError("Handoff requires requirements and completed_course_codes_entered lists")
    completed = {_code(c) for c in entered}
    if not isinstance(attribute_candidates, dict) or not isinstance(attribute_candidates.get("attributes"), dict):
        raise ValueError("Expected load_attribute_candidates output")
    seen_ids, output, skipped, course_requirements = set(), [], [], defaultdict(list)
    for requirement in requirements:
        if not isinstance(requirement, dict):
            raise ValueError("Requirement must be an object")
        rule_id = requirement.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id or rule_id in seen_ids:
            raise ValueError("Requirement IDs must be unique nonempty strings")
        seen_ids.add(rule_id)
        name = requirement.get("name")
        mapping = CATALOG_ATTRIBUTE_LABELS.get(normalize_heading(name)) if isinstance(name, str) else None
        ct_slot = (critical_thinking_candidates and degree.get("program_title") == "Computer Science, B.S."
                   and year == _SOURCE_YEAR and rule_id == "t0_row_0005"
                   and mapping == ((CRITICAL_THINKING,), False))
        if (requirement.get("kind") != "CREDIT_REQUIREMENT_DRAFT" or mapping is None
                or mapping[1] or len(mapping[0]) != 1 or (mapping[0][0] not in SUPPORTED_ATTRIBUTES and not ct_slot)):
            skipped.append({"requirement_id": rule_id, "reason": "UNSUPPORTED_REQUIREMENT"})
            continue
        category = mapping[0][0]
        item = {"requirement_id": rule_id, "requirement_name": name, "category": category,
                "remaining_credits_candidate": None, "generation_status": "SKIPPED", "reasons": [],
                "candidates": [], "withheld_courses": [], "excluded_completed_course_codes": [],
                "coverage": None}
        output.append(item)
        reserved = set()
        attr = requirement.get("attribute_filter") or {}
        if ct_slot:
            allocation = requirement.get("same_group_course_slot_allocation") or {}
            siblings = allocation.get("reserved_explicit_sibling_courses") if isinstance(allocation, dict) else None
            if (not isinstance(siblings, list) or not siblings
                    or allocation.get("scope") != "SAME_PARENT_SECTION_GROUP_ONLY"
                    or not all(isinstance(c, str) and c for c in siblings)):
                item.update(generation_status="INSUFFICIENT_EVIDENCE", reasons=["MISSING_CORE_I_SLOT_RESERVATIONS"])
                continue
            reserved = set(siblings)
            item["reserved_explicit_sibling_courses"] = sorted(reserved)
            # Older handoffs identified an honors-only match without a policy flag.
            legacy_honors_match = (requirement.get("candidate_progress_status") == "CANDIDATE_MET"
                                   and isinstance(attr, dict)
                                   and attr.get("candidate_course_codes_entered") == ["MTH 229H"])
            if legacy_honors_match or "HONORS_DISTINCT_SLOT_REVIEW_REQUIRED" in requirement.get("remaining_policy_review_flags", []):
                item.update(generation_status="INSUFFICIENT_EVIDENCE", reasons=["HONORS_DISTINCT_SLOT_REVIEW_REQUIRED"])
                continue
        if requirement.get("candidate_progress_status") == "CANDIDATE_MET":
            item["reasons"] = ["REQUIREMENT_ALREADY_CANDIDATE_COMPLETE"]
            continue
        if not isinstance(attr, dict):
            item.update(generation_status="INSUFFICIENT_EVIDENCE", reasons=["MALFORMED_REQUIREMENT_EVIDENCE"])
            continue
        if attr.get("attribute_names") != list(mapping[0]) or attr.get("match_mode") != "ANY":
            item["reasons"] = ["ATTRIBUTE_MAPPING_MISMATCH"]
            continue
        issues = _progress_issues(requirement, year, completed)
        if issues:
            item.update(generation_status="INSUFFICIENT_EVIDENCE", reasons=issues)
            continue
        remaining = requirement["credit_progress"]["additional_credits_indicated_candidate"]
        item["remaining_credits_candidate"] = remaining
        loaded = attribute_candidates["attributes"].get(category)
        if attribute_candidates.get("catalog_year") != year or loaded is None:
            item.update(generation_status="INCOMPLETE", reasons=["MISSING_OR_MISMATCHED_CATALOG_DATA"])
            continue
        item["coverage"] = deepcopy({k: v for k, v in loaded.items() if k != "courses"})
        if attribute_candidates.get("retrieval_status") not in ("VISIBLE_READS_AGREE_NOT_ATOMIC", "READ_CHANGED"):
            item.update(generation_status="INCOMPLETE", reasons=["RETRIEVAL_UNAVAILABLE"])
            continue
        available_completed = completed - reserved
        known_completed = {c["course_code"] for c in loaded["courses"] if c["course_code"] in available_completed}
        missing_completed = {m["course_code"] for m in loaded["missing_source_memberships"]} & available_completed
        recorded_completed = set(attr["candidate_course_codes_entered"])
        if missing_completed or not known_completed <= recorded_completed or (
            loaded["coverage_complete"] and known_completed != recorded_completed
        ):
            item.update(generation_status="INSUFFICIENT_EVIDENCE", reasons=["COMPLETED_CLASSIFICATION_EVIDENCE_MISMATCH"])
            continue
        for course in loaded["courses"]:
            code = course["course_code"]
            if code in completed:
                item["excluded_completed_course_codes"].append(code)
                continue
            if code in reserved:
                continue
            candidate = deepcopy(course)
            candidate["credit_fit"] = _credit_fit(course, remaining)
            reasons = list(course["issues"])
            if ct_slot:
                if course["membership_status"] != "MATCHES_REVIEWED_SOURCE":
                    reasons.append("CT_CLASSIFICATION_NOT_SOURCE_VERIFIED")
                if code == "MTH 229H":
                    reasons.append("HONORS_DISTINCT_SLOT_REVIEW_REQUIRED")
                minimum = requirement["credit_progress"]["minimum_credits_interpreted"]
                if not _number(course["min_credit_hours"]) or course["min_credit_hours"] < minimum:
                    reasons.append("CT_SINGLE_COURSE_CREDIT_FIT_UNVERIFIED")
            if course["membership_status"] not in ("MATCHES_REVIEWED_SOURCE", "YEAR_SCOPED_DATABASE_ASSOCIATION"):
                reasons.append("MEMBERSHIP_NOT_VERIFIED")
            if (course["catalog_year"] != year or course["catalog_year_id"] != attribute_candidates.get("catalog_year_id")
                    or category not in course["matched_attributes"]):
                reasons.append("MEMBERSHIP_CONTEXT_MISMATCH")
            if course["active_status"] is not True:
                reasons.append("INACTIVE_COURSE" if course["active_status"] is False else "ACTIVE_STATUS_UNKNOWN")
            if candidate["credit_fit"]["status"] in ("CREDIT_EVIDENCE_MISSING", "NO_CREDIT_CONTRIBUTION"):
                reasons.append(candidate["credit_fit"]["status"])
            if reasons:
                candidate["withheld_reasons"] = sorted(set(reasons))
                item["withheld_courses"].append(candidate)
            else:
                item["candidates"].append(candidate)
                course_requirements[code].append(rule_id)
        for key in ("candidates", "withheld_courses"):
            item[key].sort(key=lambda c: (c["course_code"], c["course_id"]))
        item["excluded_completed_course_codes"].sort()
        item["reasons"] = list(loaded["issues"])
        if item["withheld_courses"]:
            item["reasons"].append("SOME_COURSES_WITHHELD")
        item["generation_status"] = "GENERATED" if loaded["coverage_complete"] and not item["withheld_courses"] else "PARTIAL"
        if not item["candidates"]:
            item["reasons"].append("NO_USABLE_CANDIDATES_IN_RETRIEVED_DATA")
    return {"catalog_year": year, "basis": "CATALOG_ATTRIBUTE_AND_UNOFFICIAL_DEGREE_PROGRESS",
            **({"requested_categories": list(CT_AND_CORE_II_ATTRIBUTES)} if critical_thinking_candidates else {}),
            "prerequisites_evaluated": False, "registration_eligibility_determined": False,
            "source_verification": attribute_candidates.get("source_verification"),
            "retrieval_status": attribute_candidates.get("retrieval_status"),
            "retrieval_issues": deepcopy(attribute_candidates.get("issues", [])),
            "requirements": sorted(output, key=lambda r: r["requirement_id"]),
            "skipped_requirements": sorted(skipped, key=lambda r: r["requirement_id"]),
            "candidate_course_codes": sorted(course_requirements),
            "course_requirement_memberships": {c: sorted(ids) for c, ids in sorted(course_requirements.items())},
            "allocation_status": "NOT_ALLOCATED_NO_DOUBLE_COUNTING_ASSUMED"}
