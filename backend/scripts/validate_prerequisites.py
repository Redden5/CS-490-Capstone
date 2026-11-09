"""Reproducible offline full-catalog and explicit CS degree coverage report."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path

from backend.scripts.parse_prerequisites import canonical_code, parse_rows


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for child in node:
            yield from walk(child)


def make_report(catalog: list[dict], rows: list[dict], artifact: dict, degree: dict) -> dict:
    codes = {canonical_code(r["course_code"]) for r in catalog}
    courses = artifact["courses"]
    reasons = defaultdict(list)
    normalized, missing, syntax_failures = [], [], []
    noncourse, recognized = set(), Counter()
    for course in courses:
        for flag in course["review_flags"]:
            reasons[flag].append(course["course_code"])
        for rule in course["rules"]:
            for change in rule["normalizations"]:
                normalized.append({"course_code": course["course_code"], "rule_type": rule["rule_type"], **change})
            for ref in rule["missing_course_references"]:
                missing.append({"course_code": course["course_code"], "rule_type": rule["rule_type"], "reference": ref})
            for node in walk(rule["expression"]):
                if node.get("op") in {"EXTERNAL", "UNSUPPORTED"}:
                    noncourse.add(course["course_code"])
                    recognized[node.get("condition_type", "UNRECOGNIZED")] += 1
            if not rule["syntax_parsed"]:
                syntax_failures.append({"course_code": course["course_code"], "rule_type": rule["rule_type"],
                                        "raw_text": rule["raw_text"], "flags": rule["review_flags"]})
        for check in course["additional_metadata_checks"]:
            for change in check.get("normalizations", []):
                normalized.append({"course_code": course["course_code"], "metadata_field": check["field_label"], **change})
    explicit = sorted({canonical_code(code) for node in walk(degree["nodes"]) for code in node.get("course_codes", [])})
    by_code = {c["course_code"]: c for c in courses if c["catalog_year"] == degree["catalog_year"]}
    coverage = []
    for code in explicit:
        course = by_code.get(code)
        category = ("NOT_MATCHED_TO_CATALOG" if code not in codes else
                    "VERIFIED_CATALOG_NO_PUBLISHED_PREREQUISITE_METADATA" if course is None else
                    "UNRESOLVED_CONDITIONS" if course["status"] == "NEEDS_REVIEW" else "COURSE_REQUIREMENTS_REPRESENTABLE")
        coverage.append({"course_code": code, "category": category,
                         "has_prerequisite_field": bool(course and course["prerequisite_raw_text"] is not None),
                         "rule_types": [r["rule_type"] for r in course["rules"]] if course else [],
                         "review_flags": course["review_flags"] if course else [],
                         "source_url": next((r["source_url"] for r in catalog if r["course_code"] == code), None)})
    total_rules = sum(len(c["rules"]) for c in courses)
    return {
        "baseline": {"ready_for_seed_review": 1615, "needs_review": 320, "course_only_prerequisite_expressions": 1749},
        "after": {"total_catalog_courses": len(codes), **artifact["summary"],
                  "total_rule_fields": total_rules,
                  "fully_syntax_parsed_courses": sum(all(r["syntax_parsed"] for r in c["rules"]) for c in courses),
                  "courses_containing_non_course_conditions": len(noncourse),
                  "unique_missing_reference_codes": len({r["reference"] for r in missing}),
                  "courses_with_missing_references": len({r["course_code"] for r in missing}),
                  "normalized_source_expressions": len(normalized),
                  "unresolved_parent_rules_without_children": total_rules - artifact["summary"]["machine_checkable_rules"],
                  "unmatched_target_courses": sorted({c["course_code"] for c in courses} - codes)},
        "review_reasons": {f: {"course_count": len(cs), "courses": cs} for f, cs in sorted(reasons.items())},
        "external_condition_atom_counts": dict(sorted(recognized.items())),
        "normalizations": normalized, "missing_references": missing,
        "unparsed_rules": syntax_failures,
        "unresolved_relational_rules": [{"course_code": c["course_code"], "rule_type": r["rule_type"],
                                         "review_flags": c["review_flags"], "storage": "NON_MACHINE_CHECKABLE_PARENT_ONLY"}
                                        for c in courses for r in c["rules"] if not r["machine_checkable"]],
        "concurrent_metadata_review": [{"course_code": c["course_code"], "checks": c["additional_metadata_checks"]}
                                       for c in courses if "concurrent_pr_metadata_requires_review" in c["review_flags"]],
        "cr_pr_review": [{"course_code": c["course_code"], "checks": c["additional_metadata_checks"]}
                         for c in courses if "cr_pr_semantics_not_interpreted" in c["review_flags"]],
        "computer_science_bs": {"program_title": degree["program_title"], "catalog_year": degree["catalog_year"],
                                "scope": "Distinct course_codes explicitly listed in resolved nodes and nested options; no expansion of attribute buckets or electives",
                                "counts": dict(Counter(r["category"] for r in coverage)), "courses": coverage},
        "notes": ["Review flags overlap; do not sum them as distinct courses.",
                  "Machine-checkable means complete course-rule representation, not verified eligibility.",
                  "Corequisite timing policy is unresolved even for course-only syntax.",
                  "Lexical reference candidates in failed syntax are diagnostic only.",
                  "Every target can be stored as a parent; unresolved rules intentionally have no relational clauses.",
                  "No metadata establishes no eligibility claim. No database was accessed."]}


def markdown(report: dict) -> str:
    after = report["after"]
    lines = ["# Prerequisite pipeline validation", "", "Offline report; database changes remain unapplied.", "",
             "| Metric | Value |", "|---|---:|"]
    lines.extend(f"| {key} | {value} |" for key, value in after.items())
    lines += ["", "Baseline: 1,615 ready / 320 review. After: "
              f"{after['ready_for_seed_review']:,} ready / {after['needs_review']:,} review.",
              "The stricter reference validation and unresolved corequisite timing policy explain the increased review count.",
              "", "## Review reasons (overlapping course counts)", "", "| Reason | Courses |", "|---|---:|"]
    lines.extend(f"| {key} | {value['course_count']} |" for key, value in report["review_reasons"].items())
    lines += ["", "## Normalizations", ""]
    lines.extend(f"- {r['course_code']} ({r.get('rule_type', r.get('metadata_field'))}): {r['kind']}; `{r['before']}` → `{r['after']}`"
                 for r in report["normalizations"])
    lines += ["", "NUR 741 -B remains unresolved: NUR 741-B is absent from the catalog. NUR 741B is a different code and is not substituted.",
              "73 courses have only corequisite fields. The old 74-count missing/excess prerequisite flag also included MUS 214, which has two prerequisite fields.",
              "", "## Computer Science B.S. 2026–2027", "", report["computer_science_bs"]["scope"], "",
              "| Course | Coverage | Published rule types | Review flags |", "|---|---|---|---|"]
    lines.extend(f"| {r['course_code']} | {r['category']} | {', '.join(r['rule_types'])} | {', '.join(r['review_flags'])} |"
                 for r in report["computer_science_bs"]["courses"])
    lines += ["", *[f"- {note}" for note in report["notes"]], "",
              "The JSON report contains every missing reference, syntax failure, metadata discrepancy and normalized source expression.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--degree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="JSON report; Markdown sibling is also generated")
    args = parser.parse_args()
    catalog, rows = read_csv(args.catalog), read_csv(args.input)
    codes = {canonical_code(r["course_code"]) for r in catalog}
    report = make_report(catalog, rows, parse_rows(rows, codes), json.loads(args.degree.read_text(encoding="utf-8")))
    report["input_sha256"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [args.catalog, args.input, args.degree]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"after": report["after"], "cs_coverage_counts": report["computer_science_bs"]["counts"]}, indent=2))


if __name__ == "__main__":
    main()
