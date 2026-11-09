"""Parse Marshall University degree requirements into reviewable draft rules.

Version 2 handles course-count choices, credit-hour choices, wildcard course
patterns, multi-course alternatives, multiple HTML tables, and source totals.

This script does NOT connect to Supabase or approve requirements for seeding.

From the project root (rename this file to backend/scripts/parse_requirements.py):
    python -m backend.scripts.parse_requirements
    python -m backend.scripts.parse_requirements --input backend/scripts/data/degree_requirements/music_industry_ba.json
    python -m backend.scripts.parse_requirements --input file1.json --input file2.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "data" / "degree_requirements" / "computer_science_bs.json"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "data" / "degree_requirements" / "parsed"

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12,
}
NUMBER_PATTERN = r"(?:\d+|" + "|".join(NUMBER_WORDS) + r")"
SELECTION_START = re.compile(r"^\s*(?:choose|select|pick|complete)\b", re.I)
SELECTION_QUANTITY = re.compile(
    rf"^\s*(?:choose|select|pick|complete)\s+(?P<count>{NUMBER_PATTERN})"
    r"(?:\s*\(\s*\d+\s*\))?\s+(?P<following>.*)$",
    re.I,
)
CREDIT_WORD = re.compile(r"^(?:credit|credits|credit\s+hours?)\b", re.I)
COURSE_PATTERN = re.compile(r"\b(?P<subject>[A-Z][A-Z&/]{1,10})\s+(?P<level>[1-9]XX|[1-9]XXX)\b")
COURSE_LITERAL = re.compile(r"\b[A-Z][A-Z&/]{1,10}\s+\d{2,4}[A-Z]{0,3}\b")
OR_ABOVE_PATTERN = re.compile(r"\b(?:or above|or higher)\b", re.I)


def clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()


def original_label(row: dict[str, Any]) -> str:
    """Text of all non-credit columns, without duplicating the hours column."""
    non_hours = [
        clean(cell.get("text")) for cell in row.get("cells", [])
        if "hourscol" not in cell.get("classes", [])
    ]
    return clean(" ".join(non_hours)) or clean(row.get("text"))


def positive_credit(row: dict[str, Any]) -> int | float | None:
    value = row.get("credit_hours")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return value
    return None


def source_id(row: dict[str, Any]) -> str:
    return f"t{row.get('table_index', 0)}_r{row['row_index']}"


def linked_codes(row: dict[str, Any]) -> list[str]:
    """Catalog links mentioned in prose (NOT automatically required courses)."""
    seen: set[str] = set()
    found: list[str] = []
    for cell in row.get("cells", []):
        if "hourscol" in cell.get("classes", []):
            continue
        for link in cell.get("links", []):
            text = clean(link.get("text"))
            if COURSE_LITERAL.fullmatch(text) and text not in seen:
                found.append(text)
                seen.add(text)
    return found


def parse_number(word: str) -> int | None:
    word = word.lower()
    return int(word) if word.isdigit() else NUMBER_WORDS.get(word)


def parse_selection(instruction: str) -> tuple[str | None, int | None]:
    """Determine whether a selection counts options or credit hours.

    Never use a number in the 'Credit Hours' column as a course count.
    """
    instruction = clean(instruction)
    match = SELECTION_QUANTITY.match(instruction)
    if not match:
        return None, None
    quantity = parse_number(match.group("count"))
    tail = match.group("following")
    if CREDIT_WORD.match(tail):
        return "CREDITS", quantity
    if re.match(r"(?:of\s+)?(?:the\s+)?following\b", tail, re.I):
        return "COURSES", quantity
    return None, None


def pattern_from_text(text: str) -> str | None:
    match = COURSE_PATTERN.search(clean(text))
    return f"{match.group('subject')} {match.group('level')}" if match else None


def make_course_option(row: dict[str, Any]) -> dict[str, Any]:
    codes = list(row.get("course_codes") or [])
    expression = clean(row.get("code_expression"))
    flags = [f for f in (row.get("review_flags") or []) if f != "multiple_courses_in_one_row"]
    if len(codes) == 1:
        kind = "SINGLE_COURSE"
    elif len(codes) > 1 and "&" in expression:
        kind = "ALL_OF"
    else:
        kind = "UNRESOLVED_COMBINATION"
        flags.append("course_combination_semantics_unresolved")
    if OR_ABOVE_PATTERN.search(clean(row.get("text"))):
        flags.append("or_above_equivalents_not_enumerated")
    return {
        "kind": kind,
        "course_codes": codes,
        "code_expression": expression,
        "description": original_label(row),
        "declared_credit_hours": positive_credit(row),
        "source_row": row["row_index"],
        "review_flags": sorted(set(flags)),
    }


def make_pattern_option(row: dict[str, Any]) -> dict[str, Any] | None:
    label = original_label(row)
    pattern = pattern_from_text(label)
    if pattern is None:
        return None
    flags = ["course_pattern_needs_catalog_resolution"]
    # In the Music Industry catalog, the trailing 1 on MUSA 1XX is a footnote
    # marker, NOT a credit-hour value. Preserve it as original text.
    if re.search(rf"{re.escape(pattern)}\s+\d+\s*$", label):
        flags.append("trailing_number_may_be_footnote")
    return {
        "kind": "COURSE_PATTERN",
        "course_codes": [],
        "course_pattern": pattern,
        "code_expression": pattern,
        "description": label,
        "declared_credit_hours": positive_credit(row),
        "source_row": row["row_index"],
        "review_flags": flags,
    }


def interpret(raw: dict[str, Any]) -> dict[str, Any]:
    rows = raw.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Missing or empty 'rows' in scraper output")

    top_level: str | None = None
    sub_level: str | None = None
    active_table: int | None = None
    requirements: list[dict[str, Any]] = []
    ignored_rows: list[str] = []
    structural_rows: list[str] = []
    section_summaries: list[dict[str, Any]] = []
    program_flags: set[str] = set()
    unlabeled_tables: set[int] = set()
    i = 0

    def path() -> list[str]:
        return [x for x in (top_level, sub_level) if x]

    def add(rule: dict[str, Any], member_rows: list[dict[str, Any]]) -> None:
        first = member_rows[0]
        t = first.get("table_index", 0)
        rule["rule_id"] = f"t{t}_row_{first['row_index']:04d}"
        rule["table_index"] = t
        rule["source_rows"] = [r["row_index"] for r in member_rows]
        rule["source_row_ids"] = [source_id(r) for r in member_rows]
        rule["section_path"] = path()
        rule["review_flags"] = sorted(set(rule.get("review_flags") or []))
        requirements.append(rule)
        if not path():
            unlabeled_tables.add(t)

    while i < len(rows):
        row = rows[i]
        t = row.get("table_index", 0)
        if active_table != t:
            top_level = None
            sub_level = None
            active_table = t
        kind = row.get("kind")
        if kind == "column_header":
            ignored_rows.append(source_id(row))
            i += 1
            continue

        if kind == "section_header":
            label = original_label(row)
            amount = positive_credit(row)
            # Total rows, and an unexpanded 'Emphasis Area Requirements 43',
            # are not courses or children satisfying a parent requirement.
            if amount is not None:
                section_summaries.append({
                    "text": label,
                    "credits": amount,
                    "source_row_id": source_id(row),
                    "source_table_index": t,
                    "requires_external_detail": bool(re.search(r"\bemphasis\b", label, re.I)),
                })
                if re.search(r"\bemphasis\b", label, re.I):
                    program_flags.add("emphasis_area_requirements_not_enumerated_here")
            else:
                classes = row.get("row_classes") or []
                if "areaheader" in classes:
                    top_level, sub_level = label, None
                elif "areasubheader" in classes:
                    if top_level is None:
                        top_level, sub_level = label, None
                    else:
                        sub_level = label
                else:
                    top_level, sub_level = label, None
            structural_rows.append(source_id(row))
            i += 1
            continue

        if kind == "selection_instruction":
            instruction = original_label(row)
            quantity_kind, minimum = parse_selection(instruction)
            options: list[dict[str, Any]] = []
            option_rows: list[dict[str, Any]] = []
            j = i + 1
            while j < len(rows):
                nxt = rows[j]
                if nxt.get("table_index", 0) != t:
                    break
                if nxt.get("kind") in {"column_header", "section_header", "selection_instruction"}:
                    break
                if nxt.get("kind") == "course_row":
                    options.append(make_course_option(nxt))
                    option_rows.append(nxt)
                    j += 1
                    continue
                if nxt.get("kind") == "text_or_header":
                    pattern = make_pattern_option(nxt)
                    if pattern:
                        options.append(pattern)
                        option_rows.append(nxt)
                        j += 1
                        continue
                # Do NOT swallow subsequent unrelated credit requirements.
                break

            flags: list[str] = []
            if quantity_kind is None or minimum is None:
                flags.append("selection_quantity_not_understood")
            if not options:
                flags.append("selection_has_no_course_options")
            if quantity_kind == "COURSES" and minimum is not None and len(options) < minimum:
                flags.append("selection_has_fewer_options_than_requested")
            if any(opt["kind"] == "ALL_OF" for opt in options):
                flags.append("compound_options_require_group_evaluation")
            if any(opt["kind"] == "COURSE_PATTERN" for opt in options):
                flags.append("wildcard_options_need_catalog_resolution")
            if any(opt["review_flags"] for opt in options):
                flags.append("some_options_require_review")
            if re.search(r"\bwith labs?\b", instruction, re.I):
                if any(opt["kind"] == "SINGLE_COURSE" for opt in options):
                    flags.append("with_labs_instruction_has_single_course_option")
            seen: set[str] = set()
            for opt in options:
                for code in opt.get("course_codes", []):
                    if code in seen:
                        flags.append("shared_courses_across_options_need_allocation_rule")
                    seen.add(code)
            if quantity_kind == "CREDITS":
                # Credit choices are not interchangeable with 'choose N courses'.
                rule_kind = "CHOOSE_CREDITS"
                flags.append("credit_eligibility_needs_validation")
            elif quantity_kind == "COURSES":
                rule_kind = "CHOOSE_N"
            else:
                rule_kind = "UNRESOLVED_SELECTION"
            member_rows = [row] + option_rows
            add({
                "kind": rule_kind,
                "name": sub_level or top_level or instruction,
                "instruction": instruction,
                "minimum_choices": minimum if quantity_kind == "COURSES" else None,
                "minimum_credits": minimum if quantity_kind == "CREDITS" else None,
                "database_type_candidate": "MIN_CREDITS" if quantity_kind == "CREDITS" else
                    ("CHOOSE_N" if quantity_kind == "COURSES" else None),
                "catalog_declared_credits": positive_credit(row),
                "options": options,
                "review_flags": flags,
            }, member_rows)
            # An elective/science choice heading scopes its choice, not unrelated
            # following requirements such as CS 'Free Elective'.
            if sub_level is not None:
                sub_level = None
            i = j
            continue

        if kind == "course_row":
            option = make_course_option(row)
            flags = list(option["review_flags"])
            if option["kind"] != "SINGLE_COURSE":
                flags.append("standalone_combination_needs_review")
            add({
                "kind": "REQUIRED_COURSE" if option["kind"] == "SINGLE_COURSE" else "COURSE_COMBINATION",
                "name": original_label(row),
                "course_codes": option["course_codes"],
                "course_combination": option["kind"],
                "catalog_declared_credits": positive_credit(row),
                "review_flags": flags,
            }, [row])
            i += 1
            continue

        if kind == "credit_requirement":
            label = original_label(row)
            related = [row]
            j = i + 1
            while j < len(rows):
                nxt = rows[j]
                if nxt.get("table_index", 0) != t or nxt.get("kind") != kind:
                    break
                if original_label(nxt).casefold() != label.casefold():
                    break
                related.append(nxt)
                j += 1
            amounts = [positive_credit(r) for r in related]
            codes = sorted({code for r in related for code in linked_codes(r)})
            patterns = sorted({p for r in related if (p := pattern_from_text(original_label(r)))})
            flags = ["credit_requirement_eligibility_unmapped"]
            if any(a is None for a in amounts):
                flags.append("credit_amount_unresolved")
            if len(related) > 1:
                flags.append("repeated_credit_slots_need_policy_review")
            if codes:
                flags.append("embedded_course_references_require_semantic_review")
            if patterns:
                flags.append("course_pattern_needs_catalog_resolution")
            add({
                "kind": "CREDIT_REQUIREMENT_DRAFT",
                "name": label,
                "credit_slots": amounts,
                "total_listed_credits": sum(a for a in amounts if a is not None),
                "referenced_course_codes": codes,
                "course_patterns": patterns,
                "review_flags": flags,
            }, related)
            i = j
            continue

        pattern = make_pattern_option(row) if kind == "text_or_header" else None
        if pattern is not None:
            add({
                "kind": "COURSE_PATTERN_DRAFT",
                "name": original_label(row),
                "course_patterns": [pattern["course_pattern"]],
                "review_flags": pattern["review_flags"],
            }, [row])
        else:
            add({
                "kind": "UNCLASSIFIED",
                "name": original_label(row),
                "original_kind": kind,
                "raw_text": row.get("text"),
                "review_flags": ["row_needs_manual_interpretation"] + list(row.get("review_flags") or []),
            }, [row])
        i += 1

    covered = (ignored_rows + structural_rows +
               [sid for rule in requirements for sid in rule["source_row_ids"]])
    observed = [source_id(row) for row in rows]
    if len(set(covered)) != len(covered) or sorted(covered) != sorted(observed):
        raise ValueError("Row coverage mismatch; no output written")

    if unlabeled_tables:
        program_flags.add("some_requirement_tables_have_no_internal_headings")
    review_queue = [
        {"rule_id": r["rule_id"], "name": r["name"], "source_row_ids": r["source_row_ids"],
         "review_flags": r["review_flags"]}
        for r in requirements if r["review_flags"]
    ]
    counts = Counter(r["kind"] for r in requirements)
    return {
        "program_title": raw.get("program_title"),
        "catalog_year": raw.get("catalog_year"),
        "source_url": raw.get("source_url"),
        "stage": "interpreted_draft_not_approved_for_seeding",
        "summary": {
            "input_rows": len(rows),
            "interpreted_rules": len(requirements),
            "rule_types": dict(sorted(counts.items())),
            "choice_groups": counts.get("CHOOSE_N", 0) + counts.get("CHOOSE_CREDITS", 0),
            "review_queue_size": len(review_queue),
            "section_summary_rows": len(section_summaries),
        },
        "requirements": requirements,
        "review_queue": review_queue,
        "section_summaries": section_summaries,
        "program_review_flags": sorted(program_flags),
        "unlabeled_table_indices": sorted(unlabeled_tables),
        "ignored_column_header_row_ids": ignored_rows,
        "structural_section_header_row_ids": structural_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", help="Raw JSON input (repeatable)")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    inputs = args.input or [DEFAULT_INPUT]
    try:
        results: list[tuple[Path, dict[str, Any]]] = []
        for filename in inputs:
            with filename.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            result = interpret(raw)
            results.append((args.output_dir / (filename.stem + "_parsed.json"), result))
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for output, parsed in results:
            output.write_text(json.dumps(parsed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            summary = parsed["summary"]
            print(f"Program: {parsed['program_title']}")
            print(f"Source rows accounted for: {summary['input_rows']}")
            print(f"Interpreted rules: {summary['interpreted_rules']}")
            print(f"Rule types: {summary['rule_types']}")
            print(f"Rules flagged for review: {summary['review_queue_size']}")
            print(f"Program warnings: {parsed['program_review_flags']}")
            for rule in parsed["requirements"]:
                if rule["kind"] in {"CHOOSE_N", "CHOOSE_CREDITS"}:
                    amount = rule["minimum_choices"] if rule["kind"] == "CHOOSE_N" else rule["minimum_credits"]
                    unit = "courses" if rule["kind"] == "CHOOSE_N" else "credits"
                    print(f"Selection: {rule['instruction']} — {amount} {unit} from "
                          f"{len(rule['options'])} options")
            print(f"Saved: {output}\n")
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
