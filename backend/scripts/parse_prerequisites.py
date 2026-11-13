"""Parse Marshall catalog prerequisite metadata into reviewable logical rules.

Read-only, safe intermediate stage: this script does NOT modify Supabase.

Usage from repository root (PowerShell):
    python -m backend.scripts.parse_prerequisites `
      --input backend/scripts/catalog_prerequisites_test.csv `
      --catalog backend/scripts/courses_backup.csv `
      --output backend/scripts/data/prerequisites/parsed_cs_v2.json

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
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COURSE_CODE_PATTERN = r"[A-Z][A-Z0-9&]{1,7}\s+\d{2,4}[A-Z0-9]*(?:-[A-Z0-9]+)?"
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
PREREQ_LABELS = {"pr", "pre-req", "prereq", "prerequisite", "prerequisites", "pre-requisite", "pre-requisites"}
COREQ_LABELS = {"co-req", "corequisite", "corequisites", "co-requisite", "co-requisites"}
COMPOSITION_PREREQUISITE_COURSES = {
    "ENG 203", "ENG 207", "ENG 209", "ENG 210", "ENG 211", "ENG 212", "ENG 213", "ENG 214",
    "ENG 215", "ENG 220", "ENG 221", "ENG 225", "ENG 231", "ENG 232", "ENG 235", "ENG 236",
    "ENG 241", "ENG 242", "ENG 354",
}
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


def normalize_expression(text: str, catalog_codes: set[str] | None = None) -> tuple[str, list[dict]]:
    """Repair only catalog-verified suffix spacing and redundant outer punctuation.

    A missing close is repaired only for an unmatched *leading outer wrapper*.
    The candidate must parse; internal unmatched parentheses remain unresolved.
    """
    value = text.strip().rstrip(". ")
    changes = []
    if catalog_codes is not None:
        pattern = r"\b([A-Z][A-Z0-9&]{1,7}\s+\d{2,4}[A-Z0-9]*)\s*-\s*([A-Z0-9]+)\b"
        def suffix(match):
            candidate = canonical_code(match[1] + "-" + match[2])
            if candidate in catalog_codes and match[0] != candidate:
                changes.append({"kind": "catalog_verified_hyphen_spacing", "before": match[0], "after": candidate})
                return candidate
            return match[0]
        value = re.sub(pattern, suffix, value)
    candidate = None
    kind = None
    # Do not count the balanced inline concurrency parentheses specially.
    balance = 0
    minimum = 0
    for char in value:
        balance += (char == "(") - (char == ")")
        minimum = min(minimum, balance)
    if balance == -1 and minimum == -1 and value.endswith(")"):
        prefix = value[:-1].rstrip()
        depth = 0
        valid = True
        for char in prefix:
            depth += (char == "(") - (char == ")")
            valid &= depth >= 0
        if valid and depth == 0:
            candidate, kind = prefix, "removed_unmatched_terminal_close"
    elif balance == 1 and minimum == 0 and value.startswith("("):
        # The outer opening must remain unmatched through the entire expression.
        depth = 0
        outer = True
        for char in value:
            depth += (char == "(") - (char == ")")
            outer &= depth > 0
        if outer:
            candidate, kind = value + ")", "closed_unmatched_outer_wrapper"
    if candidate is not None:
        try:
            Parser(tokenize(candidate)).parse()
        except PrerequisiteParseError:
            pass
        else:
            changes.append({"kind": kind, "before": value, "after": candidate})
            value = candidate
    return value, changes


def external_condition(text: str) -> dict[str, Any]:
    """Recognize labels and literal values, never infer verification or score policy."""
    result: dict[str, Any] = {"op": "UNSUPPORTED", "raw_text": text, "verification": "UNKNOWN"}
    scored = re.fullmatch(r"(.+?) with a score of (\d+(?:\.\d+)?)", text, re.I)
    if scored:
        label, value = scored.groups()
        category = None
        if re.match(r"^(ACT|SAT)\b", label, re.I):
            category = "TEST_SCORE"
        elif label.lower().startswith("placement "):
            category = "PLACEMENT_EXAM"
        elif label.lower().startswith("admitted "):
            category = "ADMISSION"
        elif label.lower().startswith(("piano proficiency", "math workshop")):
            category = "PROFICIENCY_OR_WORKSHOP"
        if category:
            result.update(op="EXTERNAL", condition_type=category, label=label,
                          catalog_score=value, comparison="UNRESOLVED_CATALOG_SCORE_SEMANTICS")
    elif re.fullmatch(r"(?:(?:Consent|Permission)(?: of (?:the )?(?:instructor|department|Program))?|(?:Instructor|Department|Departmental) (?:permission|consent))", text, re.I):
        result.update(op="EXTERNAL", condition_type="PERMISSION", label=text)
    elif re.fullmatch(r"Admission to (?:the )?(?:Honors College|[A-Za-z ]+ (?:College|program))", text, re.I):
        result.update(op="EXTERNAL", condition_type="ADMISSION", label=text)
    elif re.fullmatch(r"(?:Freshman|Sophomore|Junior|Senior|Graduate) standing", text, re.I):
        result.update(op="EXTERNAL", condition_type="ACADEMIC_STANDING", label=text)
    return result


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
            return external_condition(normalize(" ".join(fragments)))

        raise PrerequisiteParseError(
            f"Expected course, external requirement, or parenthesized group; found {kind!r}"
        )


def contains_unsupported(node: dict[str, Any]) -> bool:
    if node["op"] in {"UNSUPPORTED", "EXTERNAL"}:
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
    if op in {"UNSUPPORTED", "EXTERNAL"}:
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


def parse_expression(text: str, catalog_codes: set[str] | None = None) -> dict[str, Any]:
    normalized, changes = normalize_expression(text, catalog_codes)
    tokens = tokenize(normalized)
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
        "normalized_text": normalized,
        "normalizations": changes,
        "syntax_parsed": True,
        "ast": ast,
        "dnf_clauses": dnf,
        "prerequisite_expression_machine_checkable": not issues,
        "issues": issues,
    }


def equivalent_prerequisite_pair(fields: list[dict], catalog_codes: set[str] | None) -> dict | None:
    try:
        first, second = [parse_expression(field["requirement_text"], catalog_codes) for field in fields]
    except PrerequisiteParseError:
        return None
    if not first["prerequisite_expression_machine_checkable"] or not second["prerequisite_expression_machine_checkable"]:
        return None
    # DNF clauses are unique and their atoms are sorted. Only OR order can differ.
    left, right = first["dnf_clauses"], second["dnf_clauses"]
    if len(left) == len(right) and all(clause in right for clause in left):
        return first
    return None


def concurrency_annotated_courses(node: dict[str, Any]) -> set[str]:
    """Course codes explicitly marked '(may be taken concurrently)' in the primary expression."""
    if node["op"] == "COURSE":
        return {node["course_code"]} if node.get("concurrent_allowed") else set()
    courses: set[str] = set()
    for child in node.get("children", []):
        courses.update(concurrency_annotated_courses(child))
    return courses


def course_atoms(node: dict | None) -> list[dict]:
    if not node:
        return []
    if node["op"] == "COURSE":
        return [node]
    return [atom for child in node.get("children", []) for atom in course_atoms(child)]


def reference_candidates(text: str) -> set[str]:
    """Lexical diagnostics even if syntax fails; these are NOT validated AST atoms."""
    pattern = r"\b[A-Z][A-Z0-9&]{1,7}\s+\d{2,4}[A-Z0-9]*(?:\s*-\s*[A-Z0-9]+)?\b"
    return {canonical_code(re.sub(r"\s*-\s*", "-", match[0])) for match in re.finditer(pattern, text)}


def concurrent_projection(node: dict) -> dict | None:
    """Annotation-only Boolean view, not a substitute for the enrollment expression."""
    if node["op"] == "COURSE":
        if node["concurrent_allowed"]:
            return {**node, "minimum_grade": None, "concurrent_allowed": False}
        return None
    children = [p for c in node.get("children", []) if (p := concurrent_projection(c))]
    return flatten(node["op"], children) if children else None


def boolean_signature(ast: dict) -> set[frozenset[str]]:
    clauses = {frozenset(a["course_code"] for a in c) for c in to_dnf(ast)}
    # Boolean absorption: A OR (A AND B) == A.
    return {c for c in clauses if not any(other < c for other in clauses)}


def audit_additional_metadata(expression: dict | None, additional: list[dict],
                              catalog_codes: set[str] | None = None) -> tuple[list[str], list[dict]]:
    flags, checks = [], []
    for row in additional:
        label = normalize(row["field_label"]).casefold()
        assessment = {"field_label": label, "requirement_text": row["requirement_text"],
                      "status": "NEEDS_REVIEW"}
        checks.append(assessment)
        if label in COREQ_LABELS:
            assessment["reason"] = "Parsed as distinct COREQUISITE; timing policy remains unresolved"
            continue
        parsed = None
        try:
            parsed = parse_expression(row["requirement_text"], catalog_codes)
            assessment["expression"] = parsed["ast"]
            assessment["normalizations"] = parsed["normalizations"]
            assessment["listed_course_codes"] = sorted({a["course_code"] for a in course_atoms(parsed["ast"])})
        except PrerequisiteParseError as error:
            assessment["parse_error"] = str(error)
        if label in {"concurrent pr", "concurrent prerequisite", "concurrent prerequisites"}:
            projected = concurrent_projection(expression["ast"]) if expression else None
            assessment["inline_concurrent_expression"] = projected
            plain = parsed and parsed["prerequisite_expression_machine_checkable"] and all(
                a["minimum_grade"] is None and not a["concurrent_allowed"] for a in course_atoms(parsed["ast"]))
            if plain and projected and boolean_signature(parsed["ast"]) == boolean_signature(projected):
                assessment["status"] = "CORROBORATES_INLINE_CONCURRENCY"
            else:
                flags.append("concurrent_pr_metadata_requires_review")
                assessment["reason"] = "Concurrent metadata Boolean expression differs from inline annotation view or cannot be parsed"
        elif label == "cr/pr":
            assessment["reason"] = "CR/PR semantics are unverified; never inferred to mean concurrency or prior completion"
            primary_codes = {a["course_code"] for a in course_atoms(expression["ast"])} if expression else set()
            listed_codes = set(assessment.get("listed_course_codes", []))
            assessment["primary_course_codes"] = sorted(primary_codes)
            assessment["course_set_discrepancy"] = primary_codes != listed_codes
            # Fall 2026 registration lists these two prior-completion alternatives for CS 120.
            if (row.get("course_code") == "CS 120" and row.get("catalog_year") == "2026-2027"
                    and row.get("source_url") == "https://catalog.marshall.edu/undergraduate/courses-az/cs/"
                    and len(additional) == 1 and row["requirement_text"] == "CS 110"
                    and expression and not expression["normalizations"]
                    and expression["normalized_text"] == (
                        "CS 110 with a minimum grade of D or CS 110H with a minimum grade of D")):
                assessment["status"] = "REVIEWED_SUPPLEMENTARY_METADATA"
                assessment["reason"] = "Fall 2026 registration (term 202701, CRN 1874) confirms the primary prerequisite."
                continue
            flags.append("cr_pr_semantics_not_interpreted")
            if primary_codes != listed_codes:
                flags.append("cr_pr_course_discrepancy")
        else:
            flags.append("additional_enrollment_metadata_not_interpreted")
            assessment["reason"] = "Unsupported additional enrollment field"
    return list(dict.fromkeys(flags)), checks


def reviewed_coursework_field(code, year, fields):
    """Select the explicit field only for these reviewed pairs of catalog texts."""
    if year != "2026-2027" or len(fields) != 2:
        return None
    by_label = {r["field_label"]: r for r in fields}
    if by_label.keys() != {"pre-req", "pr"}:
        return None
    subject = code.split()[0].lower()
    url = f"https://catalog.marshall.edu/undergraduate/courses-az/{subject}/"
    if any(r.get("source_url") != url for r in fields):
        return None
    text = by_label["pre-req"]["requirement_text"]
    summary = by_label["pr"]["requirement_text"]
    if code in COMPOSITION_PREREQUISITE_COURSES and summary == "Completion of Core II Composition Requirement":
        # ENG 203 and 207 list the same graded alternatives in a different order.
        honors = ("ENG 201H", "ENG 200H") if code in {"ENG 203", "ENG 207"} else ("ENG 200H", "ENG 201H")
        alternatives = ("ENG 201", *honors, "SCLA 102")
        if text == " or ".join(f"{course} with a minimum grade of C" for course in alternatives) + ".":
            return by_label["pre-req"]
    elif code in {"ENG 428", "ENG 429", "ENG 430"} and summary == (
        "any 200-level or 300-level ENG course EXCEPT 200, 204, 205, 314, or 354; or permission of chair"
    ):
        # Bind the shared 24-alternative source text without duplicating its course list.
        if hashlib.sha256(text.encode()).hexdigest() == "442e59fbe5c0762ae0088e1879963afb544b82c0f90fad35dcb5e51c0ead78b4":
            return by_label["pre-req"]
    elif (code == "SPN 411" and text == "SPN 315 or SPN 316 or SPN 323 or SPN 324 ."
          and summary == "SPN 315 /316 or SPN 323 /324 or permission."):
        return by_label["pre-req"]
    return None


def parse_rows(rows: list[dict[str, str]], catalog_codes: set[str] | None = None) -> dict[str, Any]:
    by_course: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        code = canonical_code(row["course_code"])
        if not row.get("field_label") or not row.get("requirement_text"):
            raise PrerequisiteParseError(f"Missing label or requirement text for {code}")
        by_course[(code, row.get("catalog_year", ""))].append(row)
    courses, counts = [], Counter()
    for (code, year), items in sorted(by_course.items()):
        primary = [r for r in items if normalize(r["field_label"]).casefold() in PREREQ_LABELS]
        coreq = [r for r in items if normalize(r["field_label"]).casefold() in COREQ_LABELS]
        additional = [r for r in items if r not in primary]
        issues = []
        if any(not re.fullmatch(r"https://catalog\.marshall\.edu/(?:undergraduate|graduate)/courses-az/[^\s?#]+/", r.get("source_url", "")) for r in items):
            issues.append("missing_or_invalid_source_url")
        if len({r.get("source_url", "") for r in items}) > 1:
            issues.append("mixed_source_urls")
        if not re.fullmatch(r"\d{4}-\d{4}", year):
            issues.append("missing_or_invalid_catalog_year")
        if catalog_codes is None:
            issues.append("catalog_reference_validation_not_performed")
        elif code not in catalog_codes:
            issues.append("target_course_not_in_catalog")
        rules = []
        expression = None
        explicit = reviewed_coursework_field(code, year, primary)
        for rule_type, fields in [("PREREQUISITE", primary), ("COREQUISITE", coreq)]:
            if not fields:
                continue
            parsed, rule_flags = None, []
            coursework_fields = [explicit] if rule_type == "PREREQUISITE" and explicit else fields
            if len(coursework_fields) != 1:
                if rule_type == "PREREQUISITE" and len(fields) == 2:
                    parsed = equivalent_prerequisite_pair(fields, catalog_codes)
                if parsed is None:
                    rule_flags.append("multiple_requirement_fields")
            else:
                try:
                    parsed = parse_expression(coursework_fields[0]["requirement_text"], catalog_codes)
                    rule_flags.extend(parsed["issues"])
                except PrerequisiteParseError as error:
                    rule_flags.append(f"expression_parse_failed: {error}")
            candidates = sorted({ref for field in coursework_fields for ref in reference_candidates(field["requirement_text"])})
            missing = sorted(ref for ref in candidates if catalog_codes is not None and ref not in catalog_codes)
            if missing:
                rule_flags.append("missing_course_references")
            if rule_type == "COREQUISITE":
                rule_flags.append("corequisite_timing_policy_unresolved")
            rules.append({"rule_type": rule_type, "raw_text": fields[0]["requirement_text"] if len(fields) == 1 else
                          "\n".join(r["requirement_text"] for r in fields),
                          "source_url": fields[0].get("source_url"),
                          "expression": parsed["ast"] if parsed else None,
                          "syntax_parsed": parsed is not None,
                          "normalized_text": parsed["normalized_text"] if parsed else None,
                          "normalizations": parsed["normalizations"] if parsed else [],
                          "course_only_dnf": parsed["dnf_clauses"] if parsed else [],
                          "course_expression_representable": bool(parsed and parsed["prerequisite_expression_machine_checkable"]),
                          "reference_candidates": candidates,
                          "missing_course_references": missing, "review_flags": rule_flags})
            issues.extend(rule_flags)
            if rule_type == "PREREQUISITE":
                expression = parsed
        if not rules:
            issues.append("no_primary_requirement_fields")
            # Metadata-only records must still yield a blocking relational rule.
            rules.append({"rule_type": "PREREQUISITE", "raw_text": "\n".join(r["requirement_text"] for r in items),
                          "source_url": items[0].get("source_url"), "expression": None, "syntax_parsed": False,
                          "normalized_text": None, "normalizations": [], "course_only_dnf": [],
                          "course_expression_representable": False, "missing_course_references": [],
                          "reference_candidates": sorted({ref for item in items for ref in reference_candidates(item["requirement_text"])}),
                          "review_flags": ["no_primary_requirement_fields"]})
        extra_flags, checks = audit_additional_metadata(expression, additional, catalog_codes)
        if explicit and code == "SPN 411":
            checks.append({"field_label": "pr", "requirement_text": next(
                r["requirement_text"] for r in primary if r["field_label"] == "pr"),
                "status": "REVIEWED_SUPPLEMENTARY_METADATA",
                "reason": "Only coursework is evaluated. The alternative permission route requires manual verification."})
        issues.extend(extra_flags)
        issues = list(dict.fromkeys(issues))
        complete = bool(rules) and not issues
        status = "READY_FOR_SEED_REVIEW" if complete else "NEEDS_REVIEW"
        # Fail closed across the course: no partial rule may suggest complete eligibility.
        for rule in rules:
            rule["machine_checkable"] = complete and rule["course_expression_representable"]
            rule["status"] = status
            rule["dnf_clauses"] = rule["course_only_dnf"] if rule["machine_checkable"] else []
        counts[status] += 1
        counts["parseable_prerequisite_expressions"] += bool(expression and expression["prerequisite_expression_machine_checkable"])
        counts["corroborated_concurrent_pr_courses"] += any(c["status"] == "CORROBORATES_INLINE_CONCURRENCY" for c in checks)
        counts["corequisite_only_courses"] += bool(coreq and not primary)
        counts["syntax_parsed_rules"] += sum(r["syntax_parsed"] for r in rules)
        counts["course_only_rules"] += sum(r["course_expression_representable"] for r in rules)
        counts["machine_checkable_rules"] += sum(r["machine_checkable"] for r in rules)
        courses.append({"course_code": code, "catalog_year": year, "source_url": items[0].get("source_url"),
                        "source_metadata": items, "rules": rules,
                        "prerequisite_raw_text": primary[0]["requirement_text"] if len(primary) == 1 else None,
                        "additional_metadata": additional, "additional_metadata_checks": checks,
                        "prerequisite_expression": expression["ast"] if expression else None,
                        "dnf_clauses": expression["dnf_clauses"] if expression else [],
                        "prerequisite_expression_machine_checkable": bool(expression and expression["prerequisite_expression_machine_checkable"]),
                        "complete_enrollment_conditions_machine_checkable": complete,
                        "status": status, "review_flags": issues})
    return {"schema_version": 2, "stage": "reviewable_prerequisite_parse_not_approved_for_seeding",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(), "approved_for_seeding": False,
            "logic_convention_for_future_relational_seeding": {"across_ruleclause_rows": "OR", "within_rulecourse_rows": "AND"},
            "summary": {"courses_with_enrollment_metadata": len(courses), "raw_metadata_rows": len(rows),
                        "parseable_prerequisite_expressions": counts["parseable_prerequisite_expressions"],
                        "ready_for_seed_review": counts["READY_FOR_SEED_REVIEW"], "needs_review": counts["NEEDS_REVIEW"],
                        **{k: counts[k] for k in ["corroborated_concurrent_pr_courses", "corequisite_only_courses",
                                                 "syntax_parsed_rules", "course_only_rules", "machine_checkable_rules"]}},
            "courses": courses, "warnings": [
                "Parsing never verifies student eligibility. Unknown evidence remains UNKNOWN.",
                "Corequisite timing policy, CR/PR, external tests and permissions remain unresolved.",
                "No metadata does not establish eligibility. Minimum grades require grade evidence.",
                "No database access or writes performed."]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="catalog_prerequisites[_test].csv")
    parser.add_argument("--output", type=Path, required=True, help="Output JSON preview")
    parser.add_argument("--catalog", type=Path, help="Full catalog CSV for course identity validation")
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
        catalog_codes = None
        if args.catalog:
            with args.catalog.open(encoding="utf-8-sig", newline="") as handle:
                catalog_codes = {canonical_code(r["course_code"]) for r in csv.DictReader(handle)}
        preview = parse_rows(rows, catalog_codes)
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
