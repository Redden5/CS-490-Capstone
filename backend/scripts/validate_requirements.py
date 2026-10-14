"""Read-only integrity checks for Marshall catalog requirement parser outputs.

Place at backend/scripts/validate_requirements.py, then run from project root:

    python -m backend.scripts.validate_requirements \
      --input backend/scripts/data/degree_requirements/parsed/computer_science_bs_parsed.json \
      --input backend/scripts/data/degree_requirements/parsed/music_industry_ba_parsed.json

Finds the matching raw extraction alongside the parsed/ directory when possible.
This tool does not access Supabase or modify any file.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ALLOWED_KINDS = {
    "REQUIRED_COURSE",
    "CHOOSE_N",
    "CHOOSE_CREDITS",
    "CREDIT_REQUIREMENT_DRAFT",
    "UNRESOLVED_SELECTION",
    "UNCLASSIFIED",
}


def sid(row: dict[str, Any]) -> str:
    return f"t{row.get('table_index', 0)}_r{row['row_index']}"


def valid_positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        result = json.load(handle)
    if not isinstance(result, dict):
        raise ValueError("JSON root must be an object")
    return result


def infer_raw_path(parsed_path: Path) -> Path:
    stem = parsed_path.stem
    if stem.endswith("_parsed"):
        stem = stem[:-len("_parsed")]
    # parsed/foo_parsed.json -> degree_requirements/foo.json
    if parsed_path.parent.name == "parsed":
        return parsed_path.parent.parent / (stem + ".json")
    return parsed_path.with_name(stem + ".json")


def validate(parsed: dict[str, Any], raw: dict[str, Any] | None) -> dict[str, Any]:
    errors: list[str] = []
    reviews: list[str] = []
    info: list[str] = []
    requirements = parsed.get("requirements", [])
    if not isinstance(requirements, list):
        requirements = []
        errors.append("'requirements' is not a list")

    summary = parsed.get("summary") or {}
    rule_types = Counter()
    covered: Counter[str] = Counter()
    ids_seen: set[str] = set()
    required_codes: defaultdict[str, list[str]] = defaultdict(list)
    flagged = 0
    selection_count = 0

    for i, rule in enumerate(requirements):
        if not isinstance(rule, dict):
            errors.append(f"Requirement #{i}: not an object")
            continue
        ident = str(rule.get("rule_id", f"index_{i}"))
        if ident in ids_seen:
            errors.append(f"Duplicate rule ID: {ident}")
        ids_seen.add(ident)
        kind = rule.get("kind")
        rule_types[kind] += 1
        if kind not in ALLOWED_KINDS:
            errors.append(f"{ident}: unknown kind {kind!r}")

        source_ids = rule.get("source_row_ids") or []
        if not source_ids:
            errors.append(f"{ident}: missing source_row_ids")
        for source in source_ids:
            covered[str(source)] += 1

        flags = rule.get("review_flags") or []
        if flags:
            flagged += 1
        if kind == "REQUIRED_COURSE":
            codes = rule.get("course_codes") or []
            if len(codes) != 1:
                errors.append(f"{ident}: REQUIRED_COURSE must have one course code; found {codes}")
            else:
                required_codes[str(codes[0])].append(ident)
        elif kind in ("CHOOSE_N", "CHOOSE_CREDITS", "UNRESOLVED_SELECTION"):
            selection_count += 1
            options = rule.get("options") or []
            if not options:
                errors.append(f"{ident}: selection without options")
            for j, option in enumerate(options):
                option_kind = option.get("kind")
                codes = option.get("course_codes") or []
                if option_kind == "SINGLE_COURSE" and len(codes) != 1:
                    errors.append(f"{ident} option {j}: SINGLE_COURSE missing single code")
                elif option_kind == "ALL_OF" and len(codes) < 2:
                    errors.append(f"{ident} option {j}: ALL_OF needs at least two codes")
                elif option_kind == "COURSE_PATTERN" and not option.get("course_pattern"):
                    errors.append(f"{ident} option {j}: COURSE_PATTERN missing pattern")
                elif option_kind not in ("SINGLE_COURSE", "ALL_OF", "COURSE_PATTERN"):
                    reviews.append(f"{ident} option {j}: unresolved option type {option_kind!r}")
            if kind == "CHOOSE_N":
                n = rule.get("minimum_choices")
                if not isinstance(n, int) or isinstance(n, bool) or n < 1:
                    errors.append(f"{ident}: CHOOSE_N has invalid minimum_choices={n!r}")
                elif n > len(options):
                    errors.append(f"{ident}: requires {n} options but has only {len(options)}")
                declared = rule.get("catalog_declared_credits")
                if valid_positive(declared) and not valid_positive(rule.get("minimum_credits")):
                    reviews.append(
                        f"{ident}: catalog specifies {declared} credits as well as a course count; "
                        "credit threshold has not been encoded in minimum_credits"
                    )
            elif kind == "CHOOSE_CREDITS":
                if not valid_positive(rule.get("minimum_credits")):
                    errors.append(f"{ident}: CHOOSE_CREDITS needs a positive minimum_credits")
        elif kind == "CREDIT_REQUIREMENT_DRAFT":
            if not valid_positive(rule.get("total_listed_credits")):
                errors.append(f"{ident}: invalid total_listed_credits")
            slots = rule.get("credit_slots") or []
            if slots and valid_positive(rule.get("total_listed_credits")):
                if sum(slots) != rule["total_listed_credits"]:
                    errors.append(f"{ident}: credit slots do not add to total")

    for value in parsed.get("ignored_column_header_row_ids", []):
        covered[str(value)] += 1
    for value in parsed.get("structural_section_header_row_ids", []):
        covered[str(value)] += 1

    if raw is not None:
        raw_rows = raw.get("rows") or []
        raw_ids = [sid(row) for row in raw_rows]
        raw_counter = Counter(raw_ids)
        for source in sorted(set(raw_counter) | set(covered)):
            got = covered[source]
            expected = raw_counter[source]
            if got != expected:
                errors.append(f"Source row {source}: accounted for {got} time(s), expected {expected}")
        if parsed.get("program_title") != raw.get("program_title"):
            errors.append("Program title does not match raw extraction")
        if parsed.get("catalog_year") != raw.get("catalog_year"):
            errors.append("Catalog year does not match raw extraction")
        raw_count = len(raw_rows)
    else:
        raw_count = summary.get("input_rows")
        reviews.append("Raw extraction not found: row completeness cannot be independently confirmed")
        if raw_count is not None and len(covered) != raw_count:
            errors.append(f"Covered {len(covered)} distinct rows; summary reports {raw_count}")

    if summary.get("interpreted_rules") != len(requirements):
        errors.append(f"Summary interpreted_rules={summary.get('interpreted_rules')} but found {len(requirements)}")
    reported_types = summary.get("rule_types") or {}
    if dict(rule_types) != reported_types:
        errors.append(f"Summary rule_types disagrees: {reported_types} vs {dict(rule_types)}")
    if summary.get("review_queue_size") != flagged:
        errors.append(f"Summary review_queue_size={summary.get('review_queue_size')} but {flagged} rules have flags")
    if summary.get("choice_groups") != selection_count:
        errors.append(f"Summary choice_groups={summary.get('choice_groups')} but {selection_count} selections found")

    repeats = {code: ids for code, ids in required_codes.items() if len(ids) > 1}
    if repeats:
        info.append("Repeated REQUIRED_COURSE references (not necessarily errors): " + "; ".join(
            f"{code} ({len(ids)} rows)" for code, ids in sorted(repeats.items())
        ))

    prog_flags = parsed.get("program_review_flags") or []
    if prog_flags:
        reviews.append("Program-level warnings: " + ", ".join(prog_flags))
    unlabeled = parsed.get("unlabeled_table_indices") or []
    if unlabeled:
        reviews.append("Tables without internal requirement headings: " + ", ".join(map(str, unlabeled)))
    if flagged:
        reviews.append(f"{flagged} requirement rules remain flagged for review")

    # No automatic approval: validation of structural consistency is not
    # equivalent to official academic approval or a successful degree audit.
    return {
        "program": parsed.get("program_title", "(unknown)"),
        "catalog_year": parsed.get("catalog_year", "(unknown)"),
        "raw_rows": raw_count,
        "accounted_rows": len(covered),
        "rules": len(requirements),
        "rule_types": dict(rule_types),
        "errors": errors,
        "reviews": reviews,
        "info": info,
        "status": "ERROR" if errors else "STRUCTURALLY_VALID_DRAFT",
        "seed_approved": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True, type=Path,
                        help="Parsed JSON path; repeat for multiple programs")
    args = parser.parse_args()
    had_errors = False
    for parsed_path in args.input:
        print("=" * 72)
        try:
            parsed = load_json(parsed_path)
            raw_path = infer_raw_path(parsed_path)
            raw = load_json(raw_path) if raw_path.is_file() else None
            result = validate(parsed, raw)
        except (OSError, ValueError, TypeError) as exc:
            print(f"ERROR reading {parsed_path}: {exc}")
            had_errors = True
            continue
        print("Program:", result["program"])
        print("Catalog year:", result["catalog_year"])
        print("Raw extraction:", raw_path if raw is not None else "NOT FOUND")
        print(f"Rows accounted for: {result['accounted_rows']}/{result['raw_rows']}")
        print("Interpreted rules:", result["rules"])
        print("Types:", result["rule_types"])
        print("Structural validation:", result["status"])
        for label, key in (("ERROR", "errors"), ("REVIEW", "reviews"), ("INFO", "info")):
            for item in result[key]:
                print(f"  {label}: {item}")
        print("Seed approval: NO (requires policy review and explicit approval)")
        had_errors |= bool(result["errors"])
    return 1 if had_errors else 0


if __name__ == "__main__":
    sys.exit(main())
