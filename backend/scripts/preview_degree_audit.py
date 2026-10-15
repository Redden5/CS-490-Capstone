"""Preview a student's course matches against a *draft* Marshall degree template.
This is a read-only prototype, not an official degree audit. It does not
verify grades, transfer equivalencies, catalog-year applicability, earned
credit hours, substitutions, or overlapping-credit policies. Choice progress is candidate-only. With Supabase,
it reports student-entered course credit estimates from fixed catalog hours,
or student-reported credit hours for variable-credit courses.
Run from the project root in PowerShell:
    python -m backend.scripts.preview_degree_audit `
      --input backend/scripts/data/degree_requirements/resolved/computer_science_bs_resolved.json `
      --completed "FYS 100, MTH 229, CS 110, CS 490, ENG 101" `
      --core-ii-json backend/scripts/data/core_ii_courses.json
Optional: --output backend/scripts/data/degree_requirements/audit_preview.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

COURSE_CODE_RE = re.compile(r"^([A-Z]{2,8})\s*([0-9]{1,4}[A-Z]?)$")
SOURCE_RULE_KINDS = {"REQUIRED_COURSE", "CHOOSE_N", "CREDIT_REQUIREMENT_DRAFT"}


class PreviewError(ValueError):
    """Invalid draft or input list."""


def canonical_course_code(raw: str) -> str:
    """Normalize explicit course codes such as 'cs110' -> 'CS 110'."""
    if not isinstance(raw, str):
        raise PreviewError(f"Expected a course-code string; got {raw!r}")
    match = COURSE_CODE_RE.fullmatch(raw.strip().upper())
    if not match:
        raise PreviewError(f"Not an explicit course code: {raw!r}")
    return f"{match.group(1)} {match.group(2)}"


def read_completed_courses(value: str | None, file_path: Path | None) -> list[str]:
    """Accept comma-separated codes and/or a JSON list of exact codes."""
    codes: list[str] = []
    if value:
        codes.extend(chunk.strip() for chunk in value.split(",") if chunk.strip())
    if file_path:
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as error:
            raise PreviewError(f"Unable to read completed course list: {error}") from error
        if isinstance(raw, dict):
            raw = raw.get("completed_courses")
        if not isinstance(raw, list):
            raise PreviewError("Completed-course JSON must be a list or {'completed_courses': [...]}.")
        codes.extend(raw)
    return sorted(set(canonical_course_code(code) for code in codes))



def parse_reported_credits(value: str | None) -> dict[str, float]:
    """Parse optional student-reported credits, e.g. 'CS 475=6,CS 110=3'."""
    if not value:
        return {}
    result: dict[str, float] = {}
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if item.count("=") != 1:
            raise PreviewError(f"Expected COURSE=credits in --reported-credits; got {item!r}")
        raw_code, raw_amount = item.split("=", 1)
        code = canonical_course_code(raw_code)
        try:
            amount = float(raw_amount.strip())
        except ValueError as error:
            raise PreviewError(f"Invalid reported credit amount for {code}") from error
        if not (0 < amount < float("inf")):
            raise PreviewError(f"Reported credits for {code} must be a finite positive number")
        if code in result:
            raise PreviewError(f"Duplicate reported credits for {code}")
        result[code] = amount
    return result


def course_credit_evidence(
    completed_courses: list[str], course_rows: list[dict[str, Any]],
    reported_credits: dict[str, float] | None = None,
) -> dict[str, dict[str, Any]]:
    """Use catalog fixed credits as estimates; never invent variable-credit earnings."""
    reported = reported_credits or {}
    completed = set(completed_courses)
    if set(reported) - completed:
        raise PreviewError(
            "Reported credits include courses not in --completed: "
            + ", ".join(sorted(set(reported) - completed))
        )
    rows: dict[str, dict[str, Any]] = {}
    for row in course_rows:
        code = canonical_course_code(row["course_code"])
        if code in rows:
            raise PreviewError(f"Duplicate catalog course row for {code}")
        rows[code] = row

    result: dict[str, dict[str, Any]] = {}
    for code in sorted(completed):
        row = rows.get(code)
        if row is None:
            if code in reported:
                raise PreviewError(f"Cannot validate reported credits: {code} absent from course table")
            result[code] = {
                "status": "COURSE_NOT_FOUND", "catalog_min_credits": None,
                "catalog_max_credits": None, "credit_value": None,
                "credit_source": None,
            }
            continue
        low, high = row.get("min_credit_hours"), row.get("max_credit_hours")
        if (not isinstance(low, (int, float)) or isinstance(low, bool)
                or not isinstance(high, (int, float)) or isinstance(high, bool)
                or not (0 < low <= high < float("inf"))):
            if code in reported:
                raise PreviewError(f"Cannot validate reported credits for {code}: invalid catalog range")
            result[code] = {
                "status": "CATALOG_CREDITS_UNKNOWN", "catalog_min_credits": low,
                "catalog_max_credits": high, "credit_value": None,
                "credit_source": None,
            }
            continue
        amount = reported.get(code)
        if amount is not None:
            if not (low <= amount <= high):
                raise PreviewError(
                    f"Reported credits for {code} ({amount:g}) outside catalog range "
                    f"{low:g}–{high:g}"
                )
            status, source, value = "REPORTED_IN_CATALOG_RANGE", "student_reported", amount
        elif low == high:
            status, source, value = "FIXED_CATALOG_ESTIMATE", "catalog_fixed_estimate", low
        else:
            status, source, value = "VARIABLE_CREDITS_NOT_REPORTED", None, None
        result[code] = {
            "status": status,
            "catalog_min_credits": low,
            "catalog_max_credits": high,
            "credit_value": value,
            "credit_source": source,
        }
    return result


def candidate_credit_progress(
    course_codes: list[str], credit_by_course: dict[str, dict[str, Any]],
    minimum_credits: int | float | None,
) -> dict[str, Any]:
    """Calculate evidence only; not a graduation or allocation decision."""
    unique_codes = sorted(set(course_codes))
    known = {
        code: credit_by_course[code]["credit_value"]
        for code in unique_codes
        if code in credit_by_course and credit_by_course[code]["credit_value"] is not None
    }
    unknown = sorted(set(unique_codes) - set(known))
    subtotal = sum(known.values())
    if not isinstance(minimum_credits, (int, float)) or minimum_credits <= 0:
        threshold = "THRESHOLD_UNSPECIFIED"
        capped = None
    else:
        capped = min(subtotal, minimum_credits)
        threshold = (
            "CANDIDATE_CREDIT_THRESHOLD_MET" if subtotal >= minimum_credits else
            "INSUFFICIENT_CREDIT_EVIDENCE" if unknown else
            "CANDIDATES_BELOW_CREDIT_THRESHOLD"
        )
    return {
        "candidate_courses_distinct": unique_codes,
        "candidate_known_credit_subtotal": subtotal,
        "candidate_credit_subtotal_capped_at_threshold": capped,
        "candidate_credit_sources": {
            code: credit_by_course[code]["credit_source"] for code in sorted(known)
        },
        "candidate_courses_without_known_credits": unknown,
        "minimum_credits": minimum_credits,
        "threshold_evidence_status": threshold,
        "requirement_satisfied": None,  # deliberately unverified
    }

def _positive_credit_threshold(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0 < value < float("inf"))


def _best_nonoverlapping_options(
    fully_entered: list[dict[str, Any]],
    credit_by_course: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Find the maximum number of course-disjoint choices, not a greedy count.

    Disjoint components are solved independently. This avoids exponential
    branching for large lists of unrelated SINGLE_COURSE electives. If a
    connected overlap component is unusually large, return unknown rather
    than giving an unverified 'maximum' as a precise value.
    """
    if not fully_entered:
        return [], True

    course_sets = [set(option["course_codes"]) for option in fully_entered]
    adjacency: list[set[int]] = [set() for _ in fully_entered]
    for i in range(len(fully_entered)):
        for j in range(i + 1, len(fully_entered)):
            if course_sets[i] & course_sets[j]:
                adjacency[i].add(j)
                adjacency[j].add(i)

    unseen = set(range(len(fully_entered)))
    components: list[list[int]] = []
    while unseen:
        start = min(unseen)
        seen = {start}
        queue = [start]
        unseen.remove(start)
        while queue:
            i = queue.pop()
            for neighbor in sorted(adjacency[i]):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    seen.add(neighbor)
                    queue.append(neighbor)
        components.append(sorted(seen))

    selected: list[int] = []
    for component in components:
        if len(component) == 1:
            selected.append(component[0])
            continue
        if len(component) > 22:
            return [], False

        best_selection: tuple[int, ...] = ()
        best_score: tuple[int, float, int] = (-1, -1, -10**9)

        def search(remaining: tuple[int, ...], chosen: tuple[int, ...], used: frozenset[str]) -> None:
            nonlocal best_selection, best_score
            # Maximum-cardinality branch-and-bound (credit evidence breaks ties).
            if len(chosen) + len(remaining) < best_score[0]:
                return
            if not remaining:
                distinct_codes = set().union(*(course_sets[i] for i in chosen)) if chosen else set()
                known = sum(
                    (credit_by_course.get(code) or {}).get("credit_value") or 0
                    for code in distinct_codes
                )
                unknown = sum(
                    (credit_by_course.get(code) or {}).get("credit_value") is None
                    for code in distinct_codes
                )
                score = (len(chosen), known, -unknown)
                if score > best_score or (score == best_score and chosen < best_selection):
                    best_score, best_selection = score, chosen
                return

            head, *tail = remaining
            if not (course_sets[head] & used):
                # Taking head makes any intersecting choices unavailable.
                keep = tuple(i for i in tail if not (course_sets[i] & course_sets[head]))
                search(keep, chosen + (head,), used | course_sets[head])
            search(tuple(tail), chosen, used)

        search(tuple(component), (), frozenset())
        selected.extend(best_selection)

    return [fully_entered[i] for i in sorted(selected)], True


def choice_requirement_progress(
    rule: dict[str, Any],
    completed: set[str],
    credit_by_course: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Report entered choices and distinct credit candidates; certify nothing.

    SINGLE_COURSE options require one exact listed course. ALL_OF options
    require *every* explicitly listed course. Equivalents, substitutions and
    any rule-level qualification stay subject to review.
    """
    raw_options = rule.get("metadata", {}).get("options", [])
    if not isinstance(raw_options, list):
        raise PreviewError(f"Malformed options for choice requirement {rule.get('name')!r}")

    option_progress: list[dict[str, Any]] = []
    fully_entered: list[dict[str, Any]] = []
    invalid_option_indices: list[int] = []

    for index, option in enumerate(raw_options, start=1):
        if not isinstance(option, dict):
            invalid_option_indices.append(index)
            continue
        kind = option.get("kind")
        codes = [canonical_course_code(c) for c in option.get("course_codes", [])]
        structurally_valid = (
            (kind == "SINGLE_COURSE" and len(codes) == 1)
            or (kind == "ALL_OF" and len(codes) >= 2 and len(set(codes)) == len(codes))
        )
        if not structurally_valid:
            invalid_option_indices.append(index)
        codes = list(dict.fromkeys(codes))
        missing = sorted(set(codes) - completed)
        entered = sorted(set(codes) & completed)
        is_fully_entered = structurally_valid and not missing
        item = {
            "option_index": index,
            "kind": kind,
            "course_codes": codes,
            "entered_course_codes": entered,
            "missing_course_codes": missing,
            "fully_entered": is_fully_entered,
            "review_flags": option.get("review_flags", []),
        }
        option_progress.append(item)
        if is_fully_entered:
            fully_entered.append(item)

    selected, exact_allocation = _best_nonoverlapping_options(fully_entered, credit_by_course or {})
    minimum = rule.get("minimum_courses")
    valid_minimum = isinstance(minimum, int) and not isinstance(minimum, bool) and minimum > 0
    count = len(selected) if exact_allocation else None
    remaining = max(0, minimum - count) if valid_minimum and count is not None else None
    if invalid_option_indices or not valid_minimum:
        choice_status = "STRUCTURE_NEEDS_REVIEW"
    elif not exact_allocation:
        choice_status = "ALLOCATION_NEEDS_REVIEW"
    elif count >= minimum:
        choice_status = "CANDIDATE_CHOICE_COUNT_MET"
    else:
        choice_status = "CANDIDATE_CHOICES_REMAIN"

    selected_courses = sorted({
        code for option in selected for code in option["course_codes"]
    })
    declared_credits = rule.get("metadata", {}).get("catalog_declared_credits")
    required_credits = rule.get("minimum_credits")
    # Catalog-declared credit counts are useful for preliminary comparisons,
    # but are *not* verified requirements when minimum_credits is null.
    if _positive_credit_threshold(required_credits):
        comparison_target = required_credits
        comparison_basis = "resolved_minimum_credits_still_subject_to_review"
    elif _positive_credit_threshold(declared_credits):
        comparison_target = declared_credits
        comparison_basis = "catalog_declared_credits_not_confirmed_as_minimum"
    else:
        comparison_target = None
        comparison_basis = "no_credit_threshold_available"

    credit_progress = (
        candidate_credit_progress(selected_courses, credit_by_course, comparison_target)
        if credit_by_course is not None and exact_allocation else None
    )

    return {
        "minimum_choices": minimum,
        "catalog_declared_credits": declared_credits,
        "resolved_minimum_credits": required_credits,
        "option_progress": option_progress,
        "candidate_options_fully_entered": [
            {"option_index": opt["option_index"], "course_codes": opt["course_codes"],
             "kind": opt["kind"], "review_flags": opt["review_flags"]}
            for opt in fully_entered
        ],
        "candidate_fully_entered_option_count": len(fully_entered),
        "candidate_max_nonoverlapping_choice_count": count,
        "candidate_selected_nonoverlapping_options": [
            {"option_index": opt["option_index"], "course_codes": opt["course_codes"],
             "kind": opt["kind"], "review_flags": opt["review_flags"]}
            for opt in selected
        ],
        "candidate_choices_remaining": remaining,
        "candidate_choice_status": choice_status,
        "invalid_option_indices": invalid_option_indices,
        "candidate_selection_exact": exact_allocation,
        "candidate_selected_course_codes_distinct": selected_courses if exact_allocation else [],
        "candidate_choice_credit_comparison_basis": comparison_basis,
        "candidate_choice_credit_progress": credit_progress,
        "requirement_satisfied": None,
    }


def classify_candidate_progress(rule: dict[str, Any], info: dict[str, Any]) -> str:
    """Describe progress in *entered data*, never confirmed degree completion.

    There are two separate questions: whether a source rule is unambiguous
    enough to evaluate, and whether entered courses/estimated credits meet it.
    A resolver READY flag does not authenticate a student's transcript.
    """
    source_status = rule.get("status")
    if source_status == "UNSUPPORTED":
        return "UNSUPPORTED"
    if source_status != "READY":
        return "SOURCE_NEEDS_REVIEW"

    kind = rule.get("source_kind")
    if kind == "REQUIRED_COURSE":
        if info.get("preview_status") == "MATCHED":
            return "CANDIDATE_MET"
        if info.get("preview_status") == "NOT_ENTERED":
            return "CANDIDATE_NOT_MET"
        return "INSUFFICIENT_EVIDENCE"

    if kind == "CHOOSE_N":
        choice_status = info.get("candidate_choice_status")
        if choice_status in {"STRUCTURE_NEEDS_REVIEW", "ALLOCATION_NEEDS_REVIEW"}:
            return "INSUFFICIENT_EVIDENCE"
        if choice_status == "CANDIDATE_CHOICES_REMAIN":
            return "CANDIDATE_NOT_MET"
        if choice_status != "CANDIDATE_CHOICE_COUNT_MET":
            return "INSUFFICIENT_EVIDENCE"

        # For a credit-bearing choice rule, *both* targets must be evaluated.
        # Treat unknown variable-credit courses as unknown, not zero credit.
        minimum_credits = rule.get("minimum_credits")
        if not _positive_credit_threshold(minimum_credits):
            return "INSUFFICIENT_EVIDENCE"
        credit = info.get("candidate_choice_credit_progress")
        if credit is None:
            return "INSUFFICIENT_EVIDENCE"
        if credit.get("threshold_evidence_status") == "CANDIDATE_CREDIT_THRESHOLD_MET":
            return "CANDIDATE_MET"
        if credit.get("candidate_courses_without_known_credits"):
            return "INSUFFICIENT_EVIDENCE"
        if credit.get("threshold_evidence_status") == "CANDIDATES_BELOW_CREDIT_THRESHOLD":
            return "CANDIDATE_NOT_MET"
        return "INSUFFICIENT_EVIDENCE"

    return "INSUFFICIENT_EVIDENCE"


# These flags request course-classification evidence, rather than an
# additional institutional policy decision. A runtime lookup can address
# them *for entered courses*, but it never approves the degree template.
ATTRIBUTE_LOOKUP_REVIEW_FLAGS = frozenset({
    "catalog_year_course_eligibility_lookup_required",
    "catalog_year_applicability_unverified",
})


def evaluate_attribute_rule_at_runtime(
    rule: dict[str, Any],
    info: dict[str, Any],
    catalog_attribute_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    """Describe candidate completion using year-scoped Supabase attributes.

    Only a *pure classification-lookup* review flag can be resolved here.
    Laboratory requirements, course-origin rules, multiple-credit-slot rules,
    unspecified eligibility, and any unfamiliar review flags remain blocked.
    Resolver source_status is never changed, and nothing is certified.
    """
    flags = set(rule.get("review_flags") or [])
    attributes = rule.get("attribute_names") or []
    metadata = rule.get("metadata") or {}
    mapping = metadata.get("attribute_resolution") or {}
    result: dict[str, Any] = {
        "status": "NOT_APPLICABLE",
        "candidate_progress_status": None,
        "original_source_status": rule.get("status"),
        "original_review_flags": sorted(flags),
        "remaining_policy_review_flags": sorted(flags - ATTRIBUTE_LOOKUP_REVIEW_FLAGS),
        "catalog_year": catalog_attribute_evidence.get("catalog_year") if catalog_attribute_evidence else None,
        "catalog_year_classification_evidence_used": False,
        "note": "Only classification eligibility can be resolved from these records; transcript verification is unavailable.",
    }
    if rule.get("source_kind") != "CREDIT_REQUIREMENT_DRAFT":
        return result
    if rule.get("status") == "UNSUPPORTED":
        result["status"] = "UNSUPPORTED"
        return result
    if not attributes:
        result["status"] = "UNMAPPED_REQUIREMENT"
        return result
    if catalog_attribute_evidence is None:
        result["status"] = "CATALOG_LOOKUP_NOT_PERFORMED"
        return result
    if not isinstance(attributes, list) or any(not isinstance(a, str) or not a for a in attributes):
        result["status"] = "INVALID_ATTRIBUTE_MAPPING"
        return result
    if (mapping.get("scope") not in {
        "catalog_year_scoped_supabase_lookup_required",
        "current_official_course_listing_only",
    } or mapping.get("attribute_match_mode") != "ANY"):
        result["status"] = "MAPPING_NOT_RUNTIME_APPROVED"
        return result
    if (mapping.get("additional_lab_eligibility_unverified") or
            flags - ATTRIBUTE_LOOKUP_REVIEW_FLAGS):
        result["status"] = "ADDITIONAL_POLICY_REVIEW_REQUIRED"
        return result
    if rule.get("status") not in {"READY", "NEEDS_REVIEW"}:
        result["status"] = "UNSUPPORTED_SOURCE_STATUS"
        return result
    if not _positive_credit_threshold(rule.get("minimum_credits")):
        result["status"] = "CREDIT_THRESHOLD_NOT_DEFINED"
        return result
    # This is populated *only* from year-scoped courseattribute SELECTs in the
    # Supabase branch, never from undated Core II or section attributes.
    progress = info.get("candidate_credit_progress")
    if progress is None or info.get("attribute_candidate_status") == "NOT_CHECKED":
        result["status"] = "CATALOG_CREDIT_EVIDENCE_MISSING"
        return result
    threshold = progress.get("threshold_evidence_status")
    if threshold == "CANDIDATE_CREDIT_THRESHOLD_MET":
        candidate = "CANDIDATE_MET"
    elif threshold == "CANDIDATES_BELOW_CREDIT_THRESHOLD":
        candidate = "CANDIDATE_NOT_MET"
    else:
        candidate = "INSUFFICIENT_EVIDENCE"
    result.update({
        "status": "CATALOG_YEAR_CLASSIFICATION_LOOKUP_APPLIED",
        "candidate_progress_status": candidate,
        "remaining_policy_review_flags": [],
        "catalog_year_classification_evidence_used": True,
        "attribute_names": list(attributes),
        "attribute_match_mode": "ANY",
        "candidate_course_codes": list(info.get("candidate_course_codes_distinct", [])),
        "note": (
            "Year-scoped course attributes and catalog-estimated/reported credits "
            "were used for candidate progress only. This does not authenticate "
            "earned credits, Marshall residency, grades, or degree eligibility."
        ),
    })
    return result



def build_course_requirement_usage(
    completed_courses: set[str],
    evaluated_rules: list[dict[str, Any]],
    credit_by_course: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Index requirement evidence by entered course; make no allocation decisions.

    A course can support multiple categories without earning extra *degree-wide*
    credits. Within a CHOOSE_N group we distinguish a selected non-overlapping
    combination from other completed or partial options. Across independent
    requirements we only flag potential overlap, never forbid or approve it.
    """
    entries: dict[str, dict[str, Any]] = {
        code: {
            "course_code": code,
            "credit_evidence": (credit_by_course or {}).get(code),
            "requirement_matches": [],
        }
        for code in sorted(completed_courses)
    }

    def record(code: str, rule: dict[str, Any], match_kind: str, **details: Any) -> None:
        if code not in entries:
            raise PreviewError(f"Rule refers to a course not entered: {code}")
        entries[code]["requirement_matches"].append({
            "rule_id": rule.get("rule_id"),
            "name": rule.get("name"),
            "source_kind": rule.get("source_kind"),
            "source_status": rule.get("source_status"),
            "candidate_progress_status": rule.get("candidate_progress_status"),
            "match_kind": match_kind,
            **details,
        })

    for rule in evaluated_rules:
        kind = rule["source_kind"]
        if kind == "REQUIRED_COURSE" and rule.get("preview_status") == "MATCHED":
            record(rule["course_code"], rule, "REQUIRED_COURSE_ENTERED")
        elif kind == "CREDIT_REQUIREMENT_DRAFT":
            matches = rule.get("possible_catalog_matches", {})
            names_by_code: dict[str, list[str]] = {}
            for name, codes in matches.items():
                for code in codes:
                    names_by_code.setdefault(code, []).append(name)
            for code, names in sorted(names_by_code.items()):
                record(code, rule, "CATALOG_ATTRIBUTE_CANDIDATE",
                       matched_attribute_names=sorted(set(names)))
            allocation = rule.get("same_group_course_slot_allocation") or {}
            for code in allocation.get("excluded_candidate_course_codes", []):
                record(code, rule, "EXCLUDED_RESERVED_CORE_I_SLOT",
                       reservation_policy=allocation.get("policy"),
                       reserved_by_rule_ids=allocation.get("reserved_by_rule_ids", {}).get(code, []))
        elif kind == "CHOOSE_N":
            selected = {
                option["option_index"]
                for option in rule.get("candidate_selected_nonoverlapping_options", [])
            }
            for option in rule.get("option_progress", []):
                index = option["option_index"]
                if option.get("fully_entered"):
                    label = ("SELECTED_WITHIN_CHOICE_GROUP" if index in selected
                             else "FULLY_ENTERED_NOT_SELECTED_WITHIN_GROUP")
                else:
                    label = "PARTIAL_CHOICE_OPTION_ENTERED"
                for code in option.get("entered_course_codes", []):
                    record(code, rule, label, option_index=index,
                           option_course_codes=option.get("course_codes", []),
                           option_review_flags=option.get("review_flags", []))

    # These represent candidate use (not merely appearing in an incomplete
    # choice). Distinct rule IDs, rather than occurrences within a rule, are
    # what matters to this diagnostic; a student's degree allocation stays open.
    candidate_kinds = {
        "REQUIRED_COURSE_ENTERED",
        "CATALOG_ATTRIBUTE_CANDIDATE",
        "SELECTED_WITHIN_CHOICE_GROUP",
    }
    overlaps: list[str] = []
    no_matches: list[str] = []
    for code, entry in entries.items():
        distinct_rules = {
            (m["rule_id"] if m["rule_id"] is not None else m["name"])
            for m in entry["requirement_matches"]
            if m["match_kind"] in candidate_kinds
        }
        entry["candidate_rule_match_count"] = len(distinct_rules)
        entry["cross_requirement_policy_status"] = (
            "MULTIPLE_CANDIDATE_REQUIREMENTS_POLICY_UNRESOLVED"
            if len(distinct_rules) > 1 else
            "ONE_CANDIDATE_REQUIREMENT" if distinct_rules else
            "NO_SELECTED_OR_DIRECT_REQUIREMENT_MATCH"
        )
        if len(distinct_rules) > 1:
            overlaps.append(code)
        if not entry["requirement_matches"]:
            no_matches.append(code)

    return {
        "stage": "read_only_course_requirement_usage_evidence",
        "policy": "Potential matches only; cross-requirement credit sharing and degree allocation are not decided.",
        "distinct_entered_course_count": len(entries),
        "courses_with_multiple_candidate_rule_matches": overlaps,
        "courses_with_no_requirement_evidence": no_matches,
        "courses": [entries[code] for code in sorted(entries)],
    }

def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise PreviewError(f"Cannot load {path}: {error}") from error
    if not isinstance(value, dict):
        raise PreviewError(f"Expected a JSON object in {path}")
    return value


def verify_resolved_draft(draft: dict[str, Any]) -> list[dict[str, Any]]:
    if draft.get("stage") != "resolved_draft_not_approved_for_seeding":
        raise PreviewError("Input must be an unapproved resolved requirements draft.")
    if draft.get("approved_for_seeding") is not False:
        raise PreviewError("Expected approved_for_seeding=false; inspect this source first.")
    nodes = draft.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise PreviewError("Resolved draft lacks nodes.")
    ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("node_id"), str):
            raise PreviewError("Malformed requirement node.")
        if node["node_id"] in ids:
            raise PreviewError(f"Duplicate node ID {node['node_id']}")
        ids.add(node["node_id"])
    for node in nodes:
        parent = node.get("parent_node_id")
        if parent is not None and parent not in ids:
            raise PreviewError(f"Unrecognized parent node for {node['node_id']}")
    rules = [node for node in nodes if node.get("source_kind") in SOURCE_RULE_KINDS]
    if len(rules) != draft.get("summary", {}).get("source_rules"):
        raise PreviewError("Rule count differs from resolver summary.")
    return rules


def core_i_ct_reserved_course_slots(
    rule: dict[str, Any], nodes: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Apply Marshall's university-wide Core I distinct CT-course rule.

    Marshall requires FYS and TWO separate CT courses. When a program's
    Core I group explicitly specifies one CT course and has another CT
    credit-requirement slot, that explicit sibling cannot fill the remaining
    slot as well. Do not reserve courses from other parent groups: legitimate
    cross-category usage is a different academic-policy question.

    This is intentionally narrow: neither two generic CT slots nor arbitrary
    credit requirements are assigned by this first allocation rule.
    """
    if rule.get("source_kind") != "CREDIT_REQUIREMENT_DRAFT":
        return None
    if set(rule.get("attribute_names") or []) != {"Critical Thinking"}:
        return None
    parent_id = rule.get("parent_node_id")
    parent = next((n for n in nodes if n.get("node_id") == parent_id), None)
    if not parent or parent.get("source_kind") != "SECTION_GROUP":
        return None
    group_name = " ".join(str(parent.get("name", "")).casefold().split())
    if not re.fullmatch(r"core (?:1|i)\s*:\s*critical thinking", group_name):
        return None

    reserved_by: dict[str, list[str]] = {}
    for sibling in nodes:
        if sibling.get("parent_node_id") != parent_id:
            continue
        if sibling.get("node_id") == rule.get("node_id"):
            continue
        if sibling.get("source_kind") != "REQUIRED_COURSE":
            continue
        sibling_rule_id = sibling.get("source", {}).get("rule_id") or sibling.get("node_id")
        for raw_code in sibling.get("course_codes", []):
            code = canonical_course_code(raw_code)
            reserved_by.setdefault(code, []).append(str(sibling_rule_id))

    return {
        "policy": "MARSHALL_CORE_I_TWO_DISTINCT_CT_COURSES_PLUS_FYS",
        "scope": "SAME_PARENT_SECTION_GROUP_ONLY",
        "group_node_id": parent_id,
        "reserved_explicit_sibling_courses": sorted(reserved_by),
        "reserved_by_rule_ids": reserved_by,
        "excluded_candidate_course_codes": [],
        "note": "First Year Seminar and each CT slot require distinct courses; "
                "overlap with Core II or major rules is evaluated separately.",
    }


def core_ii_attribute_courses(listing: dict[str, Any] | None) -> dict[str, set[str]]:
    if listing is None:
        return {}
    if listing.get("stage") != "unverified_current_core_ii_listing":
        raise PreviewError("Core II JSON must be the current, unverified listing.")
    if listing.get("catalog_year") is not None:
        raise PreviewError("Core II listing unexpectedly declares a catalog year; inspect before using.")
    categories = listing.get("categories")
    if not isinstance(categories, list):
        raise PreviewError("Core II JSON has no categories array.")
    result: dict[str, set[str]] = {}
    for group in categories:
        name = group.get("attribute_name") if isinstance(group, dict) else None
        courses = group.get("courses") if isinstance(group, dict) else None
        if not isinstance(name, str) or name in result or not isinstance(courses, list):
            raise PreviewError("Malformed or duplicated Core II category.")
        result[name] = set()
        for course in courses:
            code = course.get("course_code") if isinstance(course, dict) else None
            result[name].add(canonical_course_code(code))
    return result


def fetch_supabase_attribute_evidence(
    completed_courses: list[str], catalog_year: str,
    reported_credits: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Fetch only the student's entered courses and catalog-year attributes.

    This path executes SELECTs only. It deliberately excludes unversioned
    courseattribute rows and sectionattribute rows: neither establishes a
    course-level classification for the chosen catalog year.
    """
    import os

    try:
        from dotenv import load_dotenv
        from supabase import create_client
    except ImportError as error:
        raise PreviewError(
            "Supabase mode requires installed 'supabase' and 'python-dotenv' packages."
        ) from error

    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(dotenv_path=project_root / ".env", override=False)
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise PreviewError("Set SUPABASE_URL and SUPABASE_SECRET_KEY in your root .env file.")

    try:
        db = create_client(url, key)
    except Exception as error:
        raise PreviewError(
            f"Cannot initialize Supabase ({type(error).__name__}); check .env credentials."
        ) from error

    def fetch_rows(query: Any, description: str) -> list[dict[str, Any]]:
        """Page SELECT results to avoid PostgREST's default response limit."""
        rows: list[dict[str, Any]] = []
        page_size = 500
        while True:
            try:
                response = query.range(len(rows), len(rows) + page_size - 1).execute()
            except Exception as error:
                # Never print credentials or API exception payloads.
                raise PreviewError(
                    f"Supabase SELECT for {description} failed ({type(error).__name__}). "
                    "Check schema access, table permissions, and credentials."
                ) from error
            batch = response.data
            if not isinstance(batch, list):
                raise PreviewError(f"Unexpected Supabase response for {description}.")
            rows.extend(batch)
            if len(batch) < page_size:
                return rows

    years = fetch_rows(
        db.table("catalogyear")
          .select("catalog_year_id,catalog_label")
          .eq("catalog_label", catalog_year),
        "catalogyear",
    )
    if len(years) != 1:
        raise PreviewError(
            f"Expected one catalogyear record for {catalog_year!r}; found {len(years)}."
        )
    year_id = years[0]["catalog_year_id"]

    completed = sorted(set(completed_courses))
    courses: list[dict[str, Any]] = []
    for index in range(0, len(completed), 100):
        courses.extend(fetch_rows(
            db.table("course")
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
            db.table("courseattribute")
              .select("course_id,attribute_id,source_url,observed_at")
              .eq("catalog_year_id", year_id)
              .in_("course_id", course_ids[index:index + 100]),
            "catalog-year courseattribute",
        ))

    attribute_ids = sorted({row["attribute_id"] for row in associations})
    attrs: list[dict[str, Any]] = []
    for index in range(0, len(attribute_ids), 100):
        attrs.extend(fetch_rows(
            db.table("attribute")
              .select("attribute_id,attribute_name")
              .in_("attribute_id", attribute_ids[index:index + 100]),
            "attribute",
        ))
    attribute_by_id = {row["attribute_id"]: row["attribute_name"] for row in attrs}
    missing_attribute_ids = set(attribute_ids) - set(attribute_by_id)
    if missing_attribute_ids:
        raise PreviewError(f"Missing attribute definitions for IDs: {sorted(missing_attribute_ids)}")

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

def evaluate_preview(
    draft: dict[str, Any],
    completed_courses: list[str],
    core_ii_listing: dict[str, Any] | None = None,
    catalog_attribute_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return auditable read-only matches; never certify graduation progress."""
    rules = verify_resolved_draft(draft)
    nodes = draft["nodes"]
    completed = set(canonical_course_code(code) for code in completed_courses)
    attr_courses = core_ii_attribute_courses(core_ii_listing)
    if (catalog_attribute_evidence is not None and
            catalog_attribute_evidence["catalog_year"] != draft.get("catalog_year")):
        raise PreviewError("Supabase evidence catalog year does not match the resolved draft.")
    results: list[dict[str, Any]] = []
    all_required: set[str] = set()
    matched_required: set[str] = set()
    counts: Counter[str] = Counter()
    for rule in rules:
        kind = rule["source_kind"]
        status = rule.get("status")
        codes = [canonical_course_code(code) for code in rule.get("course_codes", [])]
        info: dict[str, Any] = {
            "rule_id": rule.get("source", {}).get("rule_id"),
            "name": rule.get("name"),
            "section_path": rule.get("section_path", []),
            "source_kind": kind,
            "source_status": status,
            "review_flags": rule.get("review_flags", []),
        }
        if kind == "REQUIRED_COURSE" and status == "READY" and len(codes) == 1:
            course = codes[0]
            all_required.add(course)
            matched = course in completed
            if matched:
                matched_required.add(course)
            info.update({
                "preview_status": "MATCHED" if matched else "NOT_ENTERED",
                "course_code": course,
                "note": "Based solely on entered course codes; completion and grades unverified.",
            })
        else:
            # Even when a rule appears potentially satisfied, unresolved
            # policies must not be presented as a completed requirement.
            info["preview_status"] = "REVIEW_REQUIRED" if status != "UNSUPPORTED" else "UNSUPPORTED"
            if kind == "CHOOSE_N":
                info.update(choice_requirement_progress(
                    rule,
                    completed,
                    (catalog_attribute_evidence["credit_by_course"]
                     if catalog_attribute_evidence is not None else None),
                ))
                info["note"] = (
                    "Candidate choice progress only. Course reuse within a choice group is "
                    "excluded, but grades, substitutions, shared allocation across other "
                    "requirements, and final credit thresholds remain unverified."
                )
            elif kind == "CREDIT_REQUIREMENT_DRAFT":
                attributes = rule.get("attribute_names", [])
                if not isinstance(attributes, list):
                    raise PreviewError(f"Malformed attribute names in rule {info['rule_id']}")
                info["minimum_credits_unverified"] = rule.get("minimum_credits")
                info["attribute_match_mode"] = rule.get("metadata", {}).get(
                    "attribute_resolution", {}
                ).get("attribute_match_mode", "ANY")
                info["eligible_attribute_names"] = attributes
                core_i_slot_policy = core_i_ct_reserved_course_slots(rule, nodes)
                if core_i_slot_policy is not None:
                    info["same_group_course_slot_allocation"] = core_i_slot_policy

                # These year-scoped rows take precedence as catalog evidence;
                # undated Core II rows, if provided, are reported separately.
                if catalog_attribute_evidence is not None and attributes:
                    year_courses = catalog_attribute_evidence["courses_by_attribute"]
                    raw_by_attribute = {
                        name: sorted(completed & year_courses.get(name, set()))
                        for name in attributes
                    }
                    if core_i_slot_policy is not None:
                        reserved = set(core_i_slot_policy["reserved_explicit_sibling_courses"])
                        core_i_slot_policy["excluded_candidate_course_codes"] = sorted(
                            reserved.intersection(
                                code for codes in raw_by_attribute.values() for code in codes
                            )
                        )
                        by_attribute = {
                            name: sorted(set(codes) - reserved)
                            for name, codes in raw_by_attribute.items()
                        }
                    else:
                        by_attribute = raw_by_attribute
                    distinct_candidates = sorted({
                        code for codes in by_attribute.values() for code in codes
                    })
                    info["possible_catalog_matches"] = by_attribute
                    info["candidate_course_codes_distinct"] = distinct_candidates
                    info["candidate_credit_progress"] = candidate_credit_progress(
                        distinct_candidates,
                        catalog_attribute_evidence["credit_by_course"],
                        rule.get("minimum_credits"),
                    )
                    info["catalog_match_evidence"] = {
                        name: {
                            code: catalog_attribute_evidence["sources"].get(name, {}).get(code, [])
                            for code in codes
                        }
                        for name, codes in by_attribute.items() if codes
                    }
                    info["attribute_candidate_status"] = (
                        "CANDIDATES_FOUND" if distinct_candidates else "NONE_ENTERED"
                    )
                elif not attributes:
                    info["attribute_candidate_status"] = "UNMAPPED"
                else:
                    info["attribute_candidate_status"] = "NOT_CHECKED"

                if core_ii_listing is not None:
                    reserved = (
                        set(core_i_slot_policy["reserved_explicit_sibling_courses"])
                        if core_i_slot_policy is not None else set()
                    )
                    info["possible_current_core_ii_matches"] = {
                        name: sorted((completed & attr_courses[name]) - reserved)
                        for name in attributes if name in attr_courses
                    }
                info["note"] = (
                    "Classification matches are candidates only. Entered course codes, "
                    "earned credit hours, grades, and all graduation policies remain unverified. "
                    "Marshall Core I distinct-slot policy is applied where identified."
                )
            else:
                info["note"] = "Requires additional review."
        info["candidate_progress_status"] = classify_candidate_progress(rule, info)
        if kind == "CREDIT_REQUIREMENT_DRAFT":
            runtime_result = evaluate_attribute_rule_at_runtime(
                rule, info, catalog_attribute_evidence
            )
            info["attribute_runtime_evaluation"] = runtime_result
            if runtime_result["catalog_year_classification_evidence_used"]:
                info["candidate_progress_status"] = runtime_result[
                    "candidate_progress_status"
                ]
        # Reported completion and fixed catalog credit hours are evidence only.
        info["transcript_verification_status"] = "UNVERIFIED"
        counts[info["preview_status"]] += 1
        results.append(info)
    course_usage = build_course_requirement_usage(
        completed,
        results,
        (catalog_attribute_evidence["credit_by_course"]
         if catalog_attribute_evidence is not None else None),
    )
    return {
        "stage": "read_only_unofficial_degree_audit_preview",
        "program_title": draft.get("program_title"),
        "catalog_year": draft.get("catalog_year"),
        "completed_course_codes_entered": sorted(completed),
        "summary": {
            "requirement_occurrences": len(rules),
            "ready_required_course_occurrences_matched": counts["MATCHED"],
            "ready_required_course_occurrences_not_entered": counts["NOT_ENTERED"],
            # This legacy key counts previews still requiring an audit review;
            # it includes READY choice rules that are not transcript-certified.
            "requirements_needing_review": counts["REVIEW_REQUIRED"],
            "source_rules_ready": sum(rule.get("status") == "READY" for rule in rules),
            "source_rules_needing_review": sum(
                rule.get("status") == "NEEDS_REVIEW" for rule in rules
            ),
            "attribute_rules_evaluated_from_catalog_year": sum(
                bool((r.get("attribute_runtime_evaluation") or {}).get(
                    "catalog_year_classification_evidence_used"))
                for r in results
            ),
            "attribute_rules_candidate_met_from_catalog_year": sum(
                (r.get("attribute_runtime_evaluation") or {}).get(
                    "candidate_progress_status") == "CANDIDATE_MET"
                for r in results
            ),
            "attribute_rules_candidate_not_met_from_catalog_year": sum(
                (r.get("attribute_runtime_evaluation") or {}).get(
                    "candidate_progress_status") == "CANDIDATE_NOT_MET"
                for r in results
            ),
            "candidate_rules_meeting_entered_requirements": sum(
                rule["candidate_progress_status"] == "CANDIDATE_MET" for rule in results
            ),
            "candidate_rules_missing_entered_requirements": sum(
                rule["candidate_progress_status"] == "CANDIDATE_NOT_MET" for rule in results
            ),
            "candidate_rules_with_insufficient_evidence": sum(
                rule["candidate_progress_status"] == "INSUFFICIENT_EVIDENCE" for rule in results
            ),
            "unsupported_rules": counts["UNSUPPORTED"],
            "unique_explicit_required_courses": len(all_required),
            "unique_explicit_required_courses_entered": len(matched_required),
            "unique_explicit_required_courses_not_entered": len(all_required - matched_required),
            "unique_missing_explicit_required_courses": sorted(all_required - matched_required),
            "core_i_ct_slots_with_reserved_course_exclusions": sum(
                bool((r.get("same_group_course_slot_allocation") or {}).get("excluded_candidate_course_codes"))
                for r in results
            ),
            "choice_requirements_with_entered_options": sum(
                bool(rule.get("candidate_options_fully_entered")) for rule in results
                if rule["source_kind"] == "CHOOSE_N"
            ),
            "credit_requirements_with_catalog_candidates": sum(
                bool(rule.get("candidate_course_codes_distinct")) for rule in results
                if rule["source_kind"] == "CREDIT_REQUIREMENT_DRAFT"
            ),
            "degree_completion_determined": False,
            "distinct_completed_courses_catalog_credit_evidence": (
                {
                    "known_credit_subtotal": sum(
                        row["credit_value"] for row in catalog_attribute_evidence["credit_by_course"].values()
                        if row["credit_value"] is not None
                    ),
                    "courses_without_known_credits": sorted(
                        code for code, row in catalog_attribute_evidence["credit_by_course"].items()
                        if row["credit_value"] is None
                    ),
                    "source": "student_reported_and_fixed_catalog_estimates_not_transcript_verified",
                } if catalog_attribute_evidence is not None else None
            ),
        },
        "rules": results,
        "course_requirement_usage": course_usage,
        "supabase_attribute_evidence": (
            {
                "catalog_year": catalog_attribute_evidence["catalog_year"],
                "catalog_year_id": catalog_attribute_evidence["catalog_year_id"],
                "year_scoped_associations_retrieved": catalog_attribute_evidence[
                    "year_scoped_associations_retrieved"
                ],
                "completed_course_credit_evidence": catalog_attribute_evidence["credit_by_course"],
                "entered_courses_missing_from_course_table": catalog_attribute_evidence[
                    "entered_courses_missing_from_course_table"
                ],
                "courses_with_no_year_scoped_attribute": catalog_attribute_evidence[
                    "courses_with_no_year_scoped_attribute"
                ],
            } if catalog_attribute_evidence is not None else None
        ),
        "warnings": [
            "UNOFFICIAL PREVIEW. Entered codes are not verified grades, credits, transfer, or transcript records.",
            "Duplicate requirements are shown separately, but unique required courses are counted only once.",
            "All flagged or unsupported requirements remain unevaluated for degree completion.",
            "Unversioned current Core II classifications, if supplied, are not catalog-year evidence.",
            "Year-scoped Supabase attributes establish catalog classifications, not earned credits or graduation compliance.",
            "Runtime classification lookup can establish candidate progress without changing the resolver's review status; lab and other policies remain unresolved.",
            "Reported credits and fixed catalog credit estimates are not transcript verification.",
            "Candidate credits across requirements may overlap; never sum them into degree-wide credits.",
            "Choice counts are non-overlapping within each CHOOSE_N group; no cross-requirement allocation is approved.",
            "Core I CT candidate slots exclude separately required sibling courses; other cross-requirement sharing remains unresolved.",
            "Course requirement usage is evidence only; a multi-match is not necessarily prohibited and is not extra earned credit.",
            "Catalog-declared credit totals on choice groups are not automatically confirmed minimum-credit rules.",
            "CANDIDATE_MET is not a graduation approval; it uses unverified course entries and catalog-estimated/reported credits.",
            "Resolver READY means the rule is structurally interpretable, not that a student's transcript meets it.",
            "No verified 120-credit degree total, GPA, substitutions, or graduation eligibility is computed.",
        ],
    }





def finalize_planning_handoff_v1(handoff: dict[str, Any]) -> dict[str, Any]:
    """Promote the existing projection to an explicit, conservative v1 interface.

    Adds only contract metadata and NOT_EVALUATED eligibility placeholders.
    No prerequisite or section-availability claims are computed here.
    """
    requirements = handoff["requirements"]
    explicit_candidate_codes: set[str] = set()
    missing_attribute_option_lists = False

    for item in requirements:
        status = item["candidate_progress_status"]
        item["planning_disposition"] = {
            "CANDIDATE_MET": "CANDIDATE_COMPLETE",
            "CANDIDATE_NOT_MET": "CANDIDATE_COURSEWORK_REMAINING",
            "SOURCE_NEEDS_REVIEW": "REVIEW_REQUIRED",
            "INSUFFICIENT_EVIDENCE": "EVIDENCE_MISSING",
            "UNSUPPORTED": "UNSUPPORTED",
        }.get(status, "REVIEW_REQUIRED")

        if item["kind"] == "REQUIRED_COURSE":
            explicit_candidate_codes.update(item.get("remaining_required_course_codes", []))
        elif item["kind"] == "CHOOSE_N":
            # Only include options for choice groups that are not candidate-complete.
            if item["planning_disposition"] != "CANDIDATE_COMPLETE":
                for option in item.get("course_options", []):
                    # A full group option is not itself an additional course to take.
                    if not option.get("fully_entered", False):
                        explicit_candidate_codes.update(
                            option.get("missing_codes_to_complete_option", [])
                        )
            # Do not imply an authoritative credit deficit for unresolved rules.
            if item["planning_disposition"] in ("REVIEW_REQUIRED", "UNSUPPORTED"):
                item["credit_progress"]["additional_credits_indicated_candidate"] = None
        elif item["kind"] == "CREDIT_REQUIREMENT_DRAFT":
            attr = item.get("attribute_filter") or {}
            if attr.get("attribute_names") and not attr.get(
                "eligible_catalog_course_options_included", False
            ) and item["planning_disposition"] != "CANDIDATE_COMPLETE":
                missing_attribute_option_lists = True

    entered = set(handoff["completed_course_codes_entered"])
    candidates = sorted(explicit_candidate_codes - entered)
    handoff["schema_version"] = "1.0.0"
    handoff["course_eligibility"] = {
        "evaluation_status": "NOT_EVALUATED",
        "default_status": "UNKNOWN",
        "courses": [
            {
                "course_code": code,
                "status": "UNKNOWN",
                "reason": "PREREQUISITES_NOT_EVALUATED",
            }
            for code in candidates
        ],
    }
    handoff["candidate_option_coverage"] = {
        "explicit_course_codes_enumerated": True,
        "unmet_attribute_option_lists_enumerated": not missing_attribute_option_lists,
        "notes": (
            "Attribute requirements with missing candidate options need a "
            "catalog-year courseattribute lookup; course_eligibility.courses "
            "is not an exhaustive course pool."
        ) if missing_attribute_option_lists else (
            "All currently unmet requirements with explicit course codes are enumerated."
        ),
    }
    return handoff


def build_planning_handoff(preview: dict[str, Any]) -> dict[str, Any]:
    """Create a narrow, *provisional* contract for the schedule-generator team.

    This is a projection of the existing read-only preview, not a new audit or
    enrollment validator. In particular, course options are requirement
    candidates, NOT courses confirmed to be offered or prerequisite-eligible.
    """
    requirements: list[dict[str, Any]] = []
    for rule in preview["rules"]:
        kind = rule["source_kind"]
        status = rule["candidate_progress_status"]
        runtime = rule.get("attribute_runtime_evaluation") or {}
        remaining_policy_flags = runtime.get("remaining_policy_review_flags")
        if remaining_policy_flags is None:
            remaining_policy_flags = list(rule.get("review_flags") or [])
        item: dict[str, Any] = {
            "rule_id": rule["rule_id"],
            "name": rule["name"],
            "section_path": rule.get("section_path", []),
            "kind": kind,
            "source_status": rule["source_status"],
            "candidate_progress_status": status,
            "transcript_verification_status": rule["transcript_verification_status"],
            "additional_coursework_indicated": (
                True if status == "CANDIDATE_NOT_MET" else
                False if status == "CANDIDATE_MET" else None
            ),
            "remaining_policy_review_flags": list(remaining_policy_flags),
            "prerequisites_evaluated": False,
            "offered_sections_evaluated": False,
        }
        if kind == "REQUIRED_COURSE":
            course = rule.get("course_code")
            item["required_course_code"] = course
            item["remaining_required_course_codes"] = (
                [course] if status == "CANDIDATE_NOT_MET" and course else []
            )
        elif kind == "CHOOSE_N":
            item["choice_progress"] = {
                "required_choices": rule.get("minimum_choices"),
                "fully_entered_options": rule.get("candidate_fully_entered_option_count"),
                "maximum_nonoverlapping_choices_entered": rule.get(
                    "candidate_max_nonoverlapping_choice_count"
                ),
                "choices_remaining_candidate": rule.get("candidate_choices_remaining"),
                "exact_within_group_allocation": rule.get("candidate_selection_exact"),
            }
            item["course_options"] = [
                {
                    "kind": option["kind"],
                    "course_codes": option["course_codes"],
                    "already_entered_codes": option["entered_course_codes"],
                    "missing_codes_to_complete_option": option["missing_course_codes"],
                    "fully_entered": option["fully_entered"],
                    "review_flags": option["review_flags"],
                }
                for option in rule.get("option_progress", [])
            ]
            item["credit_progress"] = _handoff_credit_progress(
                rule.get("candidate_choice_credit_progress"),
                rule.get("resolved_minimum_credits"),
                confirmed_target=True,
            )
        elif kind == "CREDIT_REQUIREMENT_DRAFT":
            attribute_names = rule.get("eligible_attribute_names", [])
            item["attribute_filter"] = {
                "attribute_names": attribute_names,
                "match_mode": rule.get("attribute_match_mode", "ANY"),
                "catalog_year_classification_lookup_applied": runtime.get(
                    "catalog_year_classification_evidence_used", False
                ),
                "catalog_year": runtime.get("catalog_year"),
                "candidate_course_codes_entered": rule.get(
                    "candidate_course_codes_distinct", []
                ),
                "eligible_catalog_course_options_included": False,
            }
            item["credit_progress"] = _handoff_credit_progress(
                rule.get("candidate_credit_progress"),
                rule.get("minimum_credits_unverified"),
                confirmed_target=(
                    runtime.get("catalog_year_classification_evidence_used", False)
                ),
            )
            if rule.get("same_group_course_slot_allocation"):
                item["same_group_course_slot_allocation"] = rule[
                    "same_group_course_slot_allocation"
                ]
        requirements.append(item)
    counts = Counter(r["candidate_progress_status"] for r in requirements)
    handoff = {
        "schema_version": "0.1-provisional",
        "purpose": "SCHEDULE_GENERATOR_REQUIREMENT_INPUT_ONLY",
        "degree": {
            "program_title": preview["program_title"],
            "catalog_year": preview["catalog_year"],
        },
        "completed_course_codes_entered": preview["completed_course_codes_entered"],
        "distinct_course_credit_evidence": preview["summary"].get(
            "distinct_completed_courses_catalog_credit_evidence"
        ),
        "requirements": requirements,
        "summary": {
            "candidate_met_rule_occurrences": counts["CANDIDATE_MET"],
            "candidate_not_met_rule_occurrences": counts["CANDIDATE_NOT_MET"],
            "source_review_rule_occurrences": counts["SOURCE_NEEDS_REVIEW"],
            "insufficient_evidence_rule_occurrences": counts["INSUFFICIENT_EVIDENCE"],
            "missing_explicitly_required_course_codes_distinct": preview[
                "summary"
            ]["unique_missing_explicit_required_courses"],
        },
        "scheduler_must_still_check": [
            "Prerequisites, corequisites, minimum grades and registration rules",
            "Which courses and sections are actually offered for the requested term",
            "Meeting conflicts, scheduling preferences and seat availability",
            "Any unresolved substitutions, lab qualifications or degree policy",
            "Student-reported completions, earned credits, origin and transfer equivalencies",
        ],
        "warnings": [
            "This handoff is not an approved degree audit or enrollment clearance.",
            "Options are requirement candidates, not verified enrollment-eligible courses.",
            "Do not sum per-requirement candidate credits into a degree total.",
            "Some qualifying attribute-course options are not enumerated here; query the catalog-year courseattribute data separately.",
        ],
    }
    return finalize_planning_handoff_v1(handoff)


def _handoff_credit_progress(
    progress: dict[str, Any] | None,
    required_credits: int | float | None,
    *, confirmed_target: bool,
) -> dict[str, Any]:
    """Credit shortfall is only a candidate estimate with complete evidence."""
    known = progress.get("candidate_known_credit_subtotal") if progress else None
    unknown = progress.get("candidate_courses_without_known_credits", []) if progress else []
    can_estimate = (
        confirmed_target and _positive_credit_threshold(required_credits)
        and known is not None and not unknown
    )
    return {
        "minimum_credits_interpreted": required_credits,
        "candidate_known_credit_subtotal": known,
        "candidate_courses_without_known_credits": unknown,
        "additional_credits_indicated_candidate": (
            max(0, required_credits - known) if can_estimate else None
        ),
        "earned_credits_transcript_verified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Resolved requirements JSON")
    parser.add_argument("--completed", help="Comma-separated course codes, e.g. 'CS 110,MTH 229'")
    parser.add_argument("--completed-file", type=Path, help="JSON list of completed course codes")
    parser.add_argument(
        "--reported-credits",
        help='Student-reported credits for variable-credit courses, e.g. "CS 475=6"; requires --supabase-attributes',
    )
    parser.add_argument("--core-ii-json", type=Path, help="Optional current Core II listing for candidate matches")
    parser.add_argument(
        "--supabase-attributes", action="store_true",
        help="Read entered courses' 2026-2027 (or draft-year) attribute records from Supabase",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON preview report path")
    parser.add_argument(
        "--planning-output", type=Path,
        help="Optional preliminary JSON input contract for the schedule generator",
    )
    args = parser.parse_args()
    try:
        draft = load_json(args.input)
        core_ii_listing = load_json(args.core_ii_json) if args.core_ii_json else None
        completed = read_completed_courses(args.completed, args.completed_file)
        reported_credits = parse_reported_credits(args.reported_credits)
        if reported_credits and not args.supabase_attributes:
            raise PreviewError("--reported-credits requires --supabase-attributes")
        evidence = (
            fetch_supabase_attribute_evidence(
                completed, draft.get("catalog_year"), reported_credits
            )
            if args.supabase_attributes else None
        )
        preview = evaluate_preview(draft, completed, core_ii_listing, evidence)
        if args.planning_output:
            planning = build_planning_handoff(preview)
            args.planning_output.parent.mkdir(parents=True, exist_ok=True)
            args.planning_output.write_text(
                json.dumps(planning, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(preview, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (PreviewError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    summary = preview["summary"]
    print(f"UNOFFICIAL DEGREE AUDIT PREVIEW — {preview['program_title']} ({preview['catalog_year']})")
    print(f"Completed course codes entered: {len(preview['completed_course_codes_entered'])}")
    print(f"Required-course rule occurrences matched: {summary['ready_required_course_occurrences_matched']}")
    print(f"Required-course rule occurrences not entered: {summary['ready_required_course_occurrences_not_entered']}")
    print(f"Distinct explicitly required courses entered: {summary['unique_explicit_required_courses_entered']}/{summary['unique_explicit_required_courses']}")
    print(f"Resolver rules flagged for source review: {summary['source_rules_needing_review']}")
    print(f"Choice/credit rules still in audit review: {summary['requirements_needing_review']}")
    print(f"Rules with candidate progress met (not transcript-verified): {summary['candidate_rules_meeting_entered_requirements']}")
    if args.supabase_attributes:
        print(
            "Attribute requirements evaluated with catalog-year evidence: "
            f"{summary['attribute_rules_evaluated_from_catalog_year']} "
            f"(candidate met: {summary['attribute_rules_candidate_met_from_catalog_year']}; "
            f"candidate not met: {summary['attribute_rules_candidate_not_met_from_catalog_year']})"
        )
    if summary["core_i_ct_slots_with_reserved_course_exclusions"]:
        print(
            "Core I CT slots with separately required sibling courses excluded: "
            f"{summary['core_i_ct_slots_with_reserved_course_exclusions']}"
        )
    print(f"Choice requirements with fully entered options: {summary['choice_requirements_with_entered_options']}")
    if args.supabase_attributes:
        print(
            "Credit-requirement rules with catalog-classified candidates: "
            f"{summary['credit_requirements_with_catalog_candidates']}"
        )
        print(
            "Year-scoped course-attribute rows found for entered courses: "
            f"{preview['supabase_attribute_evidence']['year_scoped_associations_retrieved']}"
        )
        credits_summary = summary["distinct_completed_courses_catalog_credit_evidence"]
        print(
            "Distinct entered courses: catalog-fixed estimates + reported credits subtotal: "
            f"{credits_summary['known_credit_subtotal']:g} (UNVERIFIED)"
        )
        if credits_summary["courses_without_known_credits"]:
            print(
                "Entered courses with undetermined credits: "
                + ", ".join(credits_summary["courses_without_known_credits"])
            )
    usage = preview["course_requirement_usage"]
    overlaps = usage["courses_with_multiple_candidate_rule_matches"]
    print(f"Entered courses with multiple candidate requirement matches: {len(overlaps)}")
    if overlaps:
        print("  Candidate overlap (policy unresolved): " + ", ".join(overlaps))
    print("\nMissing explicitly required courses (unique):")
    missing = summary["unique_missing_explicit_required_courses"]
    print("  " + (", ".join(missing) if missing else "None from the verified structural course list"))
    print("\nRequirements needing additional audit review (not transcript-certified):")
    for rule in preview["rules"]:
        if rule["preview_status"] in ("REVIEW_REQUIRED", "UNSUPPORTED"):
            evidence = ""
            if rule["source_kind"] == "CHOOSE_N":
                count = rule.get("candidate_max_nonoverlapping_choice_count")
                target = rule.get("minimum_choices")
                fully_entered = rule.get("candidate_fully_entered_option_count", 0)
                if count is None:
                    evidence = f" — {fully_entered} entered options; allocation requires review"
                else:
                    remaining = rule.get("candidate_choices_remaining")
                    evidence = (
                        f" — {count}/{target} candidate non-overlapping choices "
                        f"({remaining} more indicated; {fully_entered} fully entered options)"
                    )
                    choice_credits = rule.get("candidate_choice_credit_progress")
                    if choice_credits is not None:
                        evidence += f"; {choice_credits['candidate_known_credit_subtotal']:g} candidate choice credits"
                        if rule.get("candidate_choice_credit_comparison_basis") == "catalog_declared_credits_not_confirmed_as_minimum":
                            evidence += " (declared credit target not verified)"
            catalog_candidates = rule.get("candidate_course_codes_distinct", [])
            if catalog_candidates:
                evidence = " — catalog-attribute candidates: " + ", ".join(catalog_candidates)
                progress = rule.get("candidate_credit_progress")
                if progress:
                    evidence += (
                        f" ({progress['candidate_known_credit_subtotal']:g} "
                        "catalog-estimated/reported candidate credits"
                        f"; {progress['threshold_evidence_status']})"
                    )
            elif rule.get("possible_current_core_ii_matches"):
                candidates = rule["possible_current_core_ii_matches"]
                if any(candidates.values()):
                    evidence = " — current-listing candidates: " + ", ".join(
                        sorted(set(code for codes in candidates.values() for code in codes))
                    )
            allocation = rule.get("same_group_course_slot_allocation") or {}
            excluded = allocation.get("excluded_candidate_course_codes", [])
            if excluded:
                evidence += " — excluded from separate Core I slot: " + ", ".join(excluded)
            progress = rule.get("candidate_progress_status", "INSUFFICIENT_EVIDENCE")
            print(f"  - {rule['name']}{evidence} [{progress}]")
    if args.output:
        print(f"\nJSON preview saved: {args.output}")
    if args.planning_output:
        print(f"Provisional schedule-generator input saved: {args.planning_output}")
    print("\nREAD-ONLY. No Supabase writes. Credit evidence is NOT verified earned credit or graduation eligibility.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
