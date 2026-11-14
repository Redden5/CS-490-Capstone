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
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# Keep the existing script-level audit helpers available to Python callers.
from backend.services.degree_audit import (
    AttributeEvidenceUnavailable, PreviewError, PREREQUISITE_SCOPE_WARNING,
    apply_prerequisite_results, build_course_requirement_usage, build_planning_handoff,
    candidate_credit_progress, canonical_course_code, choice_requirement_progress,
    classify_candidate_progress, combined_candidate_codes, core_i_ct_reserved_course_slots,
    core_ii_attribute_courses, course_credit_evidence, coursework_codes,
    evaluate_attribute_rule_at_runtime, evaluate_preview, finalize_planning_handoff_v1,
    normalize_coursework, verify_resolved_draft,
)
from backend.services.degree_planning import (
    fetch_core_ii_candidates, fetch_planning_eligibility, fetch_supabase_attribute_evidence,
    normalize_reported_credits, run_degree_plan,
)
from backend.services.prerequisite_evaluator import EvaluationInputError


def _read_coursework_entries(value: str | None, file_path: Path | None) -> list[str | dict[str, Any]]:
    """Preserve student attempts and optional grades; do not choose a repeat policy."""
    codes: list = []
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
    return codes


def read_completed_coursework(value: str | None, file_path: Path | None) -> list[str | dict[str, Any]]:
    return normalize_coursework(_read_coursework_entries(value, file_path))


def read_completed_courses(value: str | None, file_path: Path | None) -> list[str]:
    """Backward-compatible code-only reader; richer evidence has a separate path."""
    return coursework_codes(read_completed_coursework(value, file_path))


def _reported_credit_entries(value: str | None) -> Iterator[tuple[str, str]]:
    for item in (value or "").split(","):
        item = item.strip()
        if not item:
            continue
        if item.count("=") != 1:
            raise PreviewError(f"Expected COURSE=credits in --reported-credits; got {item!r}")
        code, amount = item.split("=", 1)
        yield code, amount


def parse_reported_credits(value: str | None) -> dict[str, float]:
    """Parse optional student-reported credits, e.g. 'CS 475=6,CS 110=3'."""
    return normalize_reported_credits(_reported_credit_entries(value))


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise PreviewError(f"Cannot load {path}: {error}") from error
    if not isinstance(value, dict):
        raise PreviewError(f"Expected a JSON object in {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Resolved requirements JSON")
    parser.add_argument("--completed", help="Comma-separated course codes, e.g. 'CS 110,MTH 229'")
    parser.add_argument("--completed-file", type=Path, help="JSON list of course codes or {course_code, grade?} records")
    parser.add_argument(
        "--reported-credits",
        help='Student-reported credits for variable-credit courses, e.g. "CS 475=6"; requires an attribute-enabled mode',
    )
    parser.add_argument("--core-ii-json", type=Path, help="Optional current Core II listing for candidate matches")
    parser.add_argument(
        "--supabase-attributes", action="store_true",
        help="Read entered courses' 2026-2027 (or draft-year) attribute records from Supabase",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON preview report path")
    parser.add_argument(
        "--evaluate-prerequisites", action="store_true",
        help="Read catalog-year prerequisites for explicit candidates; emit evidence-only v2 planning output",
    )
    parser.add_argument(
        "--core-ii-candidates", action="store_true",
        help="Read completed-course attributes, generate Core II options and evaluate the combined pool; emit v2.1",
    )
    parser.add_argument(
        "--critical-thinking-candidates", action="store_true",
        help="Include the CS Core I Critical Thinking slot alongside Core II options; emit internal v2.2",
    )
    parser.add_argument(
        "--planning-output", type=Path,
        help="Optional preliminary JSON input contract for the schedule generator",
    )
    args = parser.parse_args()
    try:
        draft = load_json(args.input)
        core_ii_listing = load_json(args.core_ii_json) if args.core_ii_json else None
        coursework = _read_coursework_entries(args.completed, args.completed_file)
        preview, planning = run_degree_plan(
            draft, coursework, reported_credits=_reported_credit_entries(args.reported_credits),
            core_ii_listing=core_ii_listing, supabase_attributes=args.supabase_attributes,
            evaluate_prerequisites=args.evaluate_prerequisites, core_ii_candidates=args.core_ii_candidates,
            critical_thinking_candidates=args.critical_thinking_candidates,
            include_handoff=args.planning_output is not None,
        )
        if args.planning_output:
            args.planning_output.parent.mkdir(parents=True, exist_ok=True)
            args.planning_output.write_text(
                json.dumps(planning, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(preview, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (PreviewError, EvaluationInputError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    summary = preview["summary"]
    print(f"UNOFFICIAL DEGREE AUDIT PREVIEW — {preview['program_title']} ({preview['catalog_year']})")
    print(f"Completed course codes entered: {len(preview['completed_course_codes_entered'])}")
    if "course_eligibility" in preview:
        eligibility = preview["course_eligibility"]
        print(f"Prerequisite evidence: {eligibility['evaluation_status']} — "
              + ", ".join(f"{state}: {count}" for state, count in eligibility["summary"].items()))
        print("Optional grades are used for prerequisites; report only successfully completed courses."
              if args.core_ii_candidates or args.critical_thinking_candidates else PREREQUISITE_SCOPE_WARNING)
        if eligibility["evaluation_status"] in ("PARTIAL", "UNAVAILABLE"):
            print("WARNING: Prerequisite evidence is incomplete or unavailable; affected courses remain UNKNOWN.")
    if args.core_ii_candidates or args.critical_thinking_candidates:
        categories = "Core II and Critical Thinking" if args.critical_thinking_candidates else "Core II"
        flexible = preview["flexible_candidates"]
        print(f"{categories} candidates: {flexible['generation_status']} — {len(flexible['candidate_course_codes'])} distinct courses")
        if flexible["generation_status"] in ("PARTIAL", "UNAVAILABLE"):
            print(f"WARNING: {categories} candidate coverage is incomplete or unavailable; inspect flexible_candidates diagnostics.")
    print(f"Required-course rule occurrences matched: {summary['ready_required_course_occurrences_matched']}")
    print(f"Required-course rule occurrences not entered: {summary['ready_required_course_occurrences_not_entered']}")
    print(f"Distinct explicitly required courses entered: {summary['unique_explicit_required_courses_entered']}/{summary['unique_explicit_required_courses']}")
    print(f"Resolver rules flagged for source review: {summary['source_rules_needing_review']}")
    print(f"Choice/credit rules still in audit review: {summary['requirements_needing_review']}")
    print(f"Rules with candidate progress met (not transcript-verified): {summary['candidate_rules_meeting_entered_requirements']}")
    if preview["supabase_attribute_evidence"] is not None:
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
    if preview["supabase_attribute_evidence"] is not None:
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
