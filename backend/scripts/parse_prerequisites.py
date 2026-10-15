"""Parse Marshall catalog prerequisite metadata into reviewable logical rules.

Read-only, safe intermediate stage: this script does NOT modify Supabase.

Usage from repository root (PowerShell):
    python -m backend.scripts.parse_prerequisites `
      --input backend/scripts/catalog_prerequisites_test.csv `
      --output backend/scripts/data/prerequisites/parsed_cs.json

Conventions for a future seeder (NOT applied here):
    - A rule is OR across ruleclause rows.
    - Each ruleclause is AND across its rulecourse rows.
    - Convert supported expressions to disjunctive normal form (DNF).
    - Unparsed or additional enrollment conditions MUST NOT be treated as eligible.

A raw catalog entry can include placement tests or ambiguous field labels;
those records remain marked for review. Source wording is always preserved.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COURSE_CODE_PATTERN = r"[A-Z][A-Z0-9&]{1,7}\s+\d{2,4}[A-Z0-9]?(?:-[A-Z0-9]+)?"
LEXER = re.compile(
    rf"(?P<CONCURRENT>\(\s*may\s+be\s+taken\s+concurrently\s*\))"
    rf"|(?P<GRADE>\bwith\s+a\s+minimum\s+grade\s+of\s+[ABCDF][+-]?(?=\s|[).,;]|$))"
    rf"|(?P<COURSE>\b(?-i:{COURSE_CODE_PATTERN})\b)"
    rf"|(?P<AND>\band\b)"
    rf"|(?P<OR>\bor\b)"
    rf"|(?P<LPAREN>\()"
    rf"|(?P<RPAREN>\))",
    re.IGNORECASE,
)
GRADE_VALUE = re.compile(r"\bof\s+([ABCDF][+-]?)\s*$", re.IGNORECASE)
PREREQ_LABELS = {"pre-req", "prereq", "prerequisite", "prerequisites"}
MAX_DNF_CLAUSES = 256


class PrerequisiteParseError(ValueError):
    """A raw prerequisite expression cannot safely be interpreted."""


def normalize(text: str) -> str:
    return " ".join(text.split())


def canonical_code(value: str) -> str:
    match = re.fullmatch(COURSE_CODE_PATTERN, normalize(value), re.IGNORECASE)
    if not match:
        raise PrerequisiteParseError(f"Unrecognized course code: {value!r}")
    return normalize(value).upper()


def tokenize(raw: str) -> list[tuple[str, str]]:
    # Terminal punctuation is not part of the last condition.
    raw = raw.strip().rstrip(". ")
    tokens: list[tuple[str, str]] = []
    offset = 0
    for match in LEXER.finditer(raw):
        if match.start() > offset:
            other = normalize(raw[offset:match.start()])
            if other:
                tokens.append(("OTHER", other))
        tokens.append((match.lastgroup or "OTHER", normalize(match.group())))
        offset = match.end()
    if offset < len(raw):
        other = normalize(raw[offset:])
        if other:
            tokens.append(("OTHER", other))
    # A trailing period can be included in OTHER after a final course/grade.
    while tokens and tokens[-1] == ("OTHER", "."):
        tokens.pop()
    return tokens


def flatten(op: str, children: list[dict[str, Any]]) -> dict[str, Any]:
    result: list[dict[str, Any]] = []
    for child in children:
        if child.get("op") == op:
            result.extend(child["children"])
        else:
            result.append(child)
    return result[0] if len(result) == 1 else {"op": op, "children": result}


class Parser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos][0] if self.pos < len(self.tokens) else None

    def pop(self) -> tuple[str, str]:
        if self.pos >= len(self.tokens):
            raise PrerequisiteParseError("Unexpected end of expression")
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def parse(self) -> dict[str, Any]:
        if not self.tokens:
            raise PrerequisiteParseError("Empty prerequisite expression")
        result = self.parse_or()
        if self.peek() is not None:
            raise PrerequisiteParseError(f"Unexpected token: {self.tokens[self.pos]!r}")
        return result

    def parse_or(self) -> dict[str, Any]:
        children = [self.parse_and()]
        while self.peek() == "OR":
            self.pop()
            children.append(self.parse_and())
        return flatten("OR", children)

    def parse_and(self) -> dict[str, Any]:
        children = [self.parse_primary()]
        while self.peek() == "AND":
            self.pop()
            children.append(self.parse_primary())
        return flatten("AND", children)

    def parse_primary(self) -> dict[str, Any]:
        kind = self.peek()
        if kind == "LPAREN":
            self.pop()
            result = self.parse_or()
            if self.peek() != "RPAREN":
                raise PrerequisiteParseError("Unbalanced parentheses")
            self.pop()
            return result

        if kind == "COURSE":
            _, text = self.pop()
            result = {
                "op": "COURSE",
                "course_code": canonical_code(text),
                "minimum_grade": None,
                "concurrent_allowed": False,
            }
            # Catalog annotation order can be concurrency then grade or vice versa.
            while self.peek() in {"CONCURRENT", "GRADE"}:
                field_kind, value = self.pop()
                if field_kind == "CONCURRENT":
                    result["concurrent_allowed"] = True
                else:
                    match = GRADE_VALUE.search(value)
                    if match is None:
                        raise PrerequisiteParseError(f"Invalid grade expression: {value!r}")
                    if result["minimum_grade"] is not None:
                        raise PrerequisiteParseError("Repeated grade annotation on one course")
                    result["minimum_grade"] = match.group(1).upper()
            return result

        if kind == "OTHER":
            # Keep unknown non-course alternatives; never silently discard them.
            fragments: list[str] = []
            while self.peek() == "OTHER":
                fragments.append(self.pop()[1])
            return {"op": "UNSUPPORTED", "raw_text": normalize(" ".join(fragments))}

        raise PrerequisiteParseError(
            f"Expected course, external requirement, or parenthesized group; found {kind!r}"
        )


def contains_unsupported(node: dict[str, Any]) -> bool:
    if node["op"] == "UNSUPPORTED":
        return True
    return any(contains_unsupported(child) for child in node.get("children", []))


def to_dnf(node: dict[str, Any], cap: int = MAX_DNF_CLAUSES) -> list[list[dict[str, Any]]]:
    """Return OR-of-AND clauses; raise rather than truncate on explosion."""
    op = node["op"]
    if op == "COURSE":
        return [[{
            "course_code": node["course_code"],
            "minimum_grade": node["minimum_grade"],
            "concurrent_allowed": node["concurrent_allowed"],
        }]]
    if op == "UNSUPPORTED":
        raise PrerequisiteParseError("Non-course condition requires review")
    if op == "OR":
        clauses: list[list[dict[str, Any]]] = []
        for child in node["children"]:
            clauses.extend(to_dnf(child, cap))
            if len(clauses) > cap:
                raise PrerequisiteParseError("Expression expands to too many OR clauses")
        return clauses
    if op == "AND":
        clauses = [[]]
        for child in node["children"]:
            next_set: list[list[dict[str, Any]]] = []
            for prefix in clauses:
                for suffix in to_dnf(child, cap):
                    next_set.append(prefix + suffix)
                    if len(next_set) > cap:
                        raise PrerequisiteParseError("Expression expands to too many AND/OR clauses")
            clauses = next_set
        return clauses
    raise PrerequisiteParseError(f"Unexpected expression operator: {op}")


def canonicalize_clauses(clauses: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    """Deduplicate clauses and courses without weakening different conditions."""
    seen_clauses: set[str] = set()
    result: list[list[dict[str, Any]]] = []
    for clause in clauses:
        by_course: dict[str, dict[str, Any]] = {}
        for atom in clause:
            code = atom["course_code"]
            if code in by_course and by_course[code] != atom:
                raise PrerequisiteParseError(
                    f"Multiple incompatible conditions for {code} in one AND clause"
                )
            by_course[code] = atom
        normalized = [by_course[code] for code in sorted(by_course)]
        key = json.dumps(normalized, sort_keys=True)
        if key not in seen_clauses:
            seen_clauses.add(key)
            result.append(normalized)
    return result


def parse_expression(text: str) -> dict[str, Any]:
    tokens = tokenize(text)
    ast = Parser(tokens).parse()
    issues: list[str] = []
    dnf: list[list[dict[str, Any]]] = []
    if contains_unsupported(ast):
        issues.append("non_course_or_unrecognized_conditions")
    else:
        try:
            dnf = canonicalize_clauses(to_dnf(ast))
        except PrerequisiteParseError as error:
            issues.append(f"dnf_conversion_failed: {error}")
    return {
        "ast": ast,
        "dnf_clauses": dnf,
        "prerequisite_expression_machine_checkable": not issues,
        "issues": issues,
    }


def concurrency_annotated_courses(node: dict[str, Any]) -> set[str]:
    """Course codes explicitly marked '(may be taken concurrently)' in the primary expression."""
    if node["op"] == "COURSE":
        return {node["course_code"]} if node.get("concurrent_allowed") else set()
    courses: set[str] = set()
    for child in node.get("children", []):
        courses.update(concurrency_annotated_courses(child))
    return courses


def audit_additional_metadata(
    expression: dict[str, Any] | None,
    additional: list[dict[str, str]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Check consistency with primary pre-req text; never invent a new rule.

    'Concurrent PR' is treated as corroborating *only* when its entire
    course-code list exactly matches the courses already annotated as
    concurrency-permitted in the primary prerequisite expression.

    'CR/PR' is intentionally NOT treated as equivalent: its registration
    semantics and interaction with minimum-grade requirements need review.
    """
    flags: list[str] = []
    checks: list[dict[str, Any]] = []
    concurrent_listed: set[str] = set()
    concurrent_rows: list[dict[str, Any]] = []
    concurrent_parse_failed = False

    for row in additional:
        label = normalize(row["field_label"]).casefold()
        value = normalize(row["requirement_text"])
        assessment: dict[str, Any] = {
            "field_label": label,
            "requirement_text": value,
            "status": "NEEDS_REVIEW",
        }
        checks.append(assessment)

        if label == "concurrent pr":
            concurrent_rows.append(assessment)
            try:
                parsed = parse_expression(value)
                if not parsed["prerequisite_expression_machine_checkable"]:
                    raise PrerequisiteParseError("Concurrent PR contains unsupported conditions")
                # Metadata should be *only* an OR list of plain course codes.
                clauses = parsed["dnf_clauses"]
                if not clauses or any(
                    len(clause) != 1
                    or clause[0]["minimum_grade"] is not None
                    or clause[0]["concurrent_allowed"]
                    for clause in clauses
                ):
                    raise PrerequisiteParseError("Concurrent PR is not a simple course OR list")
                codes = {clause[0]["course_code"] for clause in clauses}
                concurrent_listed.update(codes)
                assessment["listed_course_codes"] = sorted(codes)
            except PrerequisiteParseError:
                assessment["reason"] = "Cannot establish a course-only OR list"
                concurrent_parse_failed = True
        elif label == "cr/pr":
            flags.append("cr_pr_semantics_not_interpreted")
            assessment["reason"] = (
                "CR/PR is not assumed equivalent to an ordinary prerequisite "
                "or to the '(may be taken concurrently)' annotation"
            )
        else:
            flags.append("additional_enrollment_metadata_not_interpreted")
            assessment["reason"] = "Unknown or unsupported additional enrollment field"

    if concurrent_rows:
        expected = concurrency_annotated_courses(expression["ast"]) if expression else set()
        consistent = (
            not concurrent_parse_failed
            and expression is not None
            and bool(expected)
            and concurrent_listed == expected
        )
        for assessment in concurrent_rows:
            assessment["inline_concurrent_course_codes"] = sorted(expected)
            if consistent:
                assessment["status"] = "CORROBORATES_INLINE_CONCURRENCY"
                assessment["note"] = (
                    "No new conditions inferred: metadata course codes exactly match "
                    "the primary expression's concurrency annotations"
                )
            else:
                assessment.setdefault(
                    "reason",
                    "Concurrent PR list does not exactly match primary-expression "
                    "concurrency annotations",
                )
        if not consistent:
            flags.append("concurrent_pr_metadata_requires_review")

    return list(dict.fromkeys(flags)), checks


def parse_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    by_course: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        code = canonical_code(row["course_code"])
        if not row.get("field_label") or not row.get("requirement_text"):
            raise PrerequisiteParseError(f"Missing label or requirement text for {code}")
        by_course[code].append(row)

    courses: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for code, items in sorted(by_course.items()):
        primary = [r for r in items if normalize(r["field_label"]).casefold() in PREREQ_LABELS]
        additional = [r for r in items if normalize(r["field_label"]).casefold() not in PREREQ_LABELS]
        texts = [normalize(r["requirement_text"]) for r in primary]
        issues: list[str] = []
        if len(texts) != 1:
            issues.append("expected_exactly_one_prerequisite_field")
        if len({r.get("catalog_year", "") for r in items}) > 1:
            issues.append("mixed_catalog_years")
        if len({r.get("source_url", "") for r in items}) > 1:
            issues.append("mixed_source_urls")

        expression = None
        if len(texts) == 1:
            try:
                expression = parse_expression(texts[0])
                issues.extend(expression["issues"])
            except PrerequisiteParseError as error:
                issues.append(f"expression_parse_failed: {error}")

        extra_flags, metadata_checks = audit_additional_metadata(expression, additional)
        issues.extend(extra_flags)
        issues = list(dict.fromkeys(issues))
        parseable = bool(expression and expression["prerequisite_expression_machine_checkable"])
        # Consistency of catalog fields does NOT independently verify a
        # student's grades, concurrent schedule, placement or registration.
        complete = parseable and not issues
        status = "READY_FOR_SEED_REVIEW" if complete else "NEEDS_REVIEW"
        counts[status] += 1
        if parseable:
            counts["parseable_prerequisite_expressions"] += 1
        if any(c["status"] == "CORROBORATES_INLINE_CONCURRENCY" for c in metadata_checks):
            counts["corroborated_concurrent_pr_courses"] += 1
        courses.append({
            "course_code": code,
            "catalog_year": items[0].get("catalog_year"),
            "source_url": items[0].get("source_url"),
            "prerequisite_raw_text": texts[0] if len(texts) == 1 else None,
            "additional_metadata": [
                {"field_label": r["field_label"], "requirement_text": r["requirement_text"]}
                for r in additional
            ],
            "additional_metadata_checks": metadata_checks,
            "prerequisite_expression": expression["ast"] if expression else None,
            "dnf_clauses": expression["dnf_clauses"] if expression else [],
            "prerequisite_expression_machine_checkable": parseable,
            "complete_enrollment_conditions_machine_checkable": complete,
            "status": status,
            "review_flags": issues,
        })

    return {
        "stage": "reviewable_prerequisite_parse_not_approved_for_seeding",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "approved_for_seeding": False,
        "logic_convention_for_future_relational_seeding": {
            "across_ruleclause_rows": "OR",
            "within_rulecourse_rows": "AND",
            "note": "Convention proposal only; no records are inserted by this script.",
        },
        "summary": {
            "courses_with_enrollment_metadata": len(courses),
            "raw_metadata_rows": len(rows),
            "parseable_prerequisite_expressions": counts["parseable_prerequisite_expressions"],
            "ready_for_seed_review": counts["READY_FOR_SEED_REVIEW"],
            "needs_review": counts["NEEDS_REVIEW"],
            "corroborated_concurrent_pr_courses": counts["corroborated_concurrent_pr_courses"],
        },
        "courses": courses,
        "warnings": [
            "This is a parser preview, not an official eligibility decision.",
            "Matching Concurrent PR metadata only corroborates primary-expression concurrency annotations.",
            "CR/PR, external tests, or unmatched additional metadata remain unresolved.",
            "No prerequisite record does not mean a course has no enrollment restrictions.",
            "Minimum grade, concurrent enrollment, and external test scores require appropriate student input.",
            "No Supabase writes were performed.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="catalog_prerequisites[_test].csv")
    parser.add_argument("--output", type=Path, required=True, help="Output JSON preview")
    args = parser.parse_args()
    try:
        with args.input.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"course_code", "field_label", "requirement_text"}
            if not required.issubset(reader.fieldnames or []):
                raise PrerequisiteParseError(
                    f"CSV missing fields: {sorted(required - set(reader.fieldnames or []))}"
                )
            rows = list(reader)
        preview = parse_rows(rows)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(preview, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (OSError, ValueError, csv.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    stats = preview["summary"]
    print(f"Courses with metadata: {stats['courses_with_enrollment_metadata']}")
    print(f"Raw metadata records: {stats['raw_metadata_rows']}")
    print(f"Prerequisite expressions parsed as course-only: {stats['parseable_prerequisite_expressions']}")
    print(f"Ready for seeding review: {stats['ready_for_seed_review']}")
    print(f"Need review: {stats['needs_review']}")
    print(f"Concurrent PR metadata matching inline annotations: {stats['corroborated_concurrent_pr_courses']}")
    print("\nExample courses:")
    for course in preview["courses"]:
        if course["course_code"] in {"CS 110", "CS 120", "CS 210", "CS 215"}:
            print(f"  {course['course_code']}: {course['status']}; "
                  f"DNF clauses={len(course['dnf_clauses'])}; "
                  f"flags={course['review_flags']}")
    print(f"Saved: {args.output}")
    print("READ-ONLY. No Supabase writes or eligibility claims.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
