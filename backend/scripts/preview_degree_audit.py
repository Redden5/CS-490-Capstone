"""Preview a student's course matches against a *draft* Marshall degree template.

This is a read-only prototype, not an official degree audit. It does not
verify grades, transfer equivalencies, catalog-year applicability, credit
hours earned, substitutions, or overlapping-credit policies.

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


def evaluate_preview(
    draft: dict[str, Any],
    completed_courses: list[str],
    core_ii_listing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return auditable read-only matches; never certify graduation progress."""
    rules = verify_resolved_draft(draft)
    completed = set(canonical_course_code(code) for code in completed_courses)
    attr_courses = core_ii_attribute_courses(core_ii_listing)
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
                options = rule.get("metadata", {}).get("options", [])
                if not isinstance(options, list):
                    options = []
                possible: list[dict[str, Any]] = []
                for option in options:
                    option_codes = [canonical_course_code(x) for x in option.get("course_codes", [])]
                    if option_codes and set(option_codes).issubset(completed):
                        possible.append({"course_codes": option_codes, "kind": option.get("kind")})
                info["candidate_options_fully_entered"] = possible
                info["minimum_choices"] = rule.get("minimum_courses")
                info["note"] = (
                    "Candidate options only; minimum credits, combinations, "
                    "shared-course allocation, and qualifications may be unresolved."
                )
            elif kind == "CREDIT_REQUIREMENT_DRAFT":
                attributes = rule.get("attribute_names", [])
                matched_for_category: dict[str, list[str]] = {}
                for name in attributes:
                    if name in attr_courses:
                        matched_for_category[name] = sorted(completed & attr_courses[name])
                info["minimum_credits_unverified"] = rule.get("minimum_credits")
                info["possible_current_core_ii_matches"] = matched_for_category
                info["note"] = (
                    "Candidate matches from a current, unversioned listing; "
                    "earned credits and policy eligibility are not verified."
                )
            else:
                info["note"] = "Requires additional review."
        counts[info["preview_status"]] += 1
        results.append(info)

    return {
        "stage": "read_only_unofficial_degree_audit_preview",
        "program_title": draft.get("program_title"),
        "catalog_year": draft.get("catalog_year"),
        "completed_course_codes_entered": sorted(completed),
        "summary": {
            "requirement_occurrences": len(rules),
            "ready_required_course_occurrences_matched": counts["MATCHED"],
            "ready_required_course_occurrences_not_entered": counts["NOT_ENTERED"],
            "requirements_needing_review": counts["REVIEW_REQUIRED"],
            "unsupported_rules": counts["UNSUPPORTED"],
            "unique_explicit_required_courses": len(all_required),
            "unique_explicit_required_courses_entered": len(matched_required),
            "unique_explicit_required_courses_not_entered": len(all_required - matched_required),
            "unique_missing_explicit_required_courses": sorted(all_required - matched_required),
            "degree_completion_determined": False,
        },
        "rules": results,
        "warnings": [
            "UNOFFICIAL PREVIEW. Entered codes are not verified grades, credits, transfer, or transcript records.",
            "Duplicate requirements are shown separately, but unique required courses are counted only once.",
            "All flagged or unsupported requirements remain unevaluated for degree completion.",
            "Current Core II course classifications are not verified for historical catalog years.",
            "No 120-credit degree total, GPA, substitutions, or graduation eligibility is computed.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Resolved requirements JSON")
    parser.add_argument("--completed", help="Comma-separated course codes, e.g. 'CS 110,MTH 229'")
    parser.add_argument("--completed-file", type=Path, help="JSON list of completed course codes")
    parser.add_argument("--core-ii-json", type=Path, help="Optional current Core II listing for candidate matches")
    parser.add_argument("--output", type=Path, help="Optional JSON preview report path")
    args = parser.parse_args()

    try:
        draft = load_json(args.input)
        core_ii_listing = load_json(args.core_ii_json) if args.core_ii_json else None
        completed = read_completed_courses(args.completed, args.completed_file)
        preview = evaluate_preview(draft, completed, core_ii_listing)
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
    print(f"Requirements still needing review: {summary['requirements_needing_review']}")
    print("\nMissing explicitly required courses (unique):")
    missing = summary["unique_missing_explicit_required_courses"]
    print("  " + (", ".join(missing) if missing else "None from the verified structural course list"))
    print("\nUnresolved requirements (never auto-completed):")
    for rule in preview["rules"]:
        if rule["preview_status"] in ("REVIEW_REQUIRED", "UNSUPPORTED"):
            evidence = ""
            if rule.get("candidate_options_fully_entered"):
                evidence = f" — {len(rule['candidate_options_fully_entered'])} candidate option(s) fully entered"
            candidates = rule.get("possible_current_core_ii_matches", {})
            if any(candidates.values()):
                evidence = " — current-listing candidates: " + ", ".join(
                    sorted(set(code for codes in candidates.values() for code in codes))
                )
            print(f"  - {rule['name']}{evidence}")
    if args.output:
        print(f"\nJSON preview saved: {args.output}")
    print("\nREAD-ONLY. No Supabase writes. No graduation or credit-total determination.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
