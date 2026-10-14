"""Compare Marshall's current Core II listing with term-specific section attributes.

Read-only Supabase access. Creates a local CSV report; never modifies Supabase.

Run from repository root:
    python -m backend.scripts.compare_core_ii --term-id 1
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

DEFAULT_INPUT = Path("backend/scripts/data/core_ii_courses.json")
DEFAULT_OUTPUT = Path("backend/scripts/data/core_ii_comparison_fall_2026.csv")


def chunks(items, size=75):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start : start + size]


def paged_select(db, table, columns, *, equals=None, in_filters=None):
    """Read all matching records without relying on PostgREST's default 1000-row limit."""
    page_size = 500
    offset = 0
    while True:
        query = db.table(table).select(columns)
        for key, value in (equals or {}).items():
            query = query.eq(key, value)
        for key, values in (in_filters or {}).items():
            query = query.in_(key, values)
        # Consistent ordering is essential when results span multiple pages.
        ordering = {
            "course": ("course_id",),
            "attribute": ("attribute_id",),
            "coursesection": ("section_id",),
            "sectionattribute": ("section_id", "attribute_id"),
        }.get(table, ())
        for column in ordering:
            query = query.order(column)
        result = query.range(offset, offset + page_size - 1).execute()
        records = result.data or []
        yield from records
        if len(records) < page_size:
            return
        offset += page_size


def select_batched(db, table, columns, key, values, *, equals=None):
    for part in chunks(sorted(set(values))):
        yield from paged_select(
            db, table, columns, equals=equals, in_filters={key: part}
        )


def analyze(official_pairs, offered_by_course, tagged_sections_by_pair, course_code_by_id):
    """Produce one row per official or term-tagged course/attribute pair."""
    offered_counts = {
        course_code_by_id[cid]: len(sids)
        for cid, sids in offered_by_course.items()
        if cid in course_code_by_id
    }
    tag_counts = {
        pair: len(section_ids) for pair, section_ids in tagged_sections_by_pair.items()
    }
    all_pairs = sorted(official_pairs | set(tag_counts))
    rows = []
    for course_code, attribute_name in all_pairs:
        is_official = (course_code, attribute_name) in official_pairs
        offering_count = offered_counts.get(course_code, 0)
        tagged_count = tag_counts.get((course_code, attribute_name), 0)
        if is_official:
            if offering_count == 0:
                status = "OFFICIAL_NOT_OFFERED_THIS_TERM"
            elif tagged_count == offering_count:
                status = "AGREEMENT"
            elif tagged_count > 0:
                status = "PARTIALLY_TAGGED"
            else:
                status = "OFFICIAL_OFFERED_NOT_TAGGED"
        else:
            status = "TERM_TAGGED_NOT_ON_CURRENT_LIST"
        rows.append({
            "course_code": course_code,
            "attribute_name": attribute_name,
            "on_official_list": is_official,
            "offered_sections": offering_count,
            "sections_with_attribute": tagged_count,
            "status": status,
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--term-id", type=int, default=1, help="Fall 2026 is 1 in our seeded database")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    official_pairs = {
        (course["course_code"], category["attribute_name"])
        for category in payload["categories"]
        for course in category["courses"]
    }
    codes = {code for code, _ in official_pairs}
    attribute_names = {attribute for _, attribute in official_pairs}

    load_dotenv(".env")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not os.getenv("SUPABASE_URL") or not key:
        raise SystemExit("Missing SUPABASE_URL or SUPABASE_SECRET_KEY in .env")
    db = create_client(os.environ["SUPABASE_URL"], key)

    courses = list(select_batched(db, "course", "course_id,course_code", "course_code", codes))
    ids_by_code = {c["course_code"]: c["course_id"] for c in courses}
    if set(ids_by_code) != codes:
        raise SystemExit(f"Missing database courses: {sorted(codes - set(ids_by_code))}")

    attributes = list(select_batched(db, "attribute", "attribute_id,attribute_name", "attribute_name", attribute_names))
    name_by_attr_id = {a["attribute_id"]: a["attribute_name"] for a in attributes}
    if set(name_by_attr_id.values()) != attribute_names:
        raise SystemExit(f"Missing database attributes: {sorted(attribute_names - set(name_by_attr_id.values()))}")

    # First find any officially listed courses offered this term, whether tagged or not.
    offered_sections = list(select_batched(
        db, "coursesection", "section_id,course_id", "course_id",
        ids_by_code.values(), equals={"term_id": args.term_id},
    ))
    offered_by_course = defaultdict(set)
    section_course_id = {}
    for section in offered_sections:
        offered_by_course[section["course_id"]].add(section["section_id"])
        section_course_id[section["section_id"]] = section["course_id"]

    # Get ALL sections tagged with one of these attributes, including courses
    # absent from the official public list. Then restrict to the requested term.
    tagged_records = list(select_batched(
        db, "sectionattribute", "section_id,attribute_id", "attribute_id", name_by_attr_id
    ))
    tagged_section_ids = {record["section_id"] for record in tagged_records}
    tagged_section_details = list(select_batched(
        db, "coursesection", "section_id,course_id", "section_id",
        tagged_section_ids, equals={"term_id": args.term_id},
    ))
    for section in tagged_section_details:
        section_course_id[section["section_id"]] = section["course_id"]
    relevant_section_ids = {s["section_id"] for s in tagged_section_details}

    all_course_ids = set(offered_by_course) | {s["course_id"] for s in tagged_section_details}
    course_code_by_id = {course["course_id"]: course["course_code"] for course in courses}
    missing_ids = all_course_ids - set(course_code_by_id)
    if missing_ids:
        for course in select_batched(db, "course", "course_id,course_code", "course_id", missing_ids):
            course_code_by_id[course["course_id"]] = course["course_code"]
    if all_course_ids - set(course_code_by_id):
        raise SystemExit("Some section course IDs could not be resolved")

    tagged_sections_by_pair = defaultdict(set)
    for record in tagged_records:
        sid = record["section_id"]
        if sid not in relevant_section_ids:
            continue
        cid = section_course_id[sid]
        code = course_code_by_id[cid]
        attr = name_by_attr_id[record["attribute_id"]]
        tagged_sections_by_pair[(code, attr)].add(sid)

    rows = analyze(official_pairs, offered_by_course, tagged_sections_by_pair, course_code_by_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "course_code", "attribute_name", "on_official_list",
            "offered_sections", "sections_with_attribute", "status",
        ])
        writer.writeheader()
        writer.writerows(rows)

    counts = Counter(row["status"] for row in rows)
    print(f"Term ID: {args.term_id}")
    print(f"Official course-category associations: {len(official_pairs)}")
    print(f"Official distinct courses: {len(codes)}")
    print(f"Official courses offered this term: {len(set(ids_by_code) & {course_code_by_id[cid] for cid in offered_by_course})}")
    print(f"Fall term-tagged course-category associations: {len(tagged_sections_by_pair)}")
    print("Comparison:")
    for status in (
        "AGREEMENT", "PARTIALLY_TAGGED", "OFFICIAL_OFFERED_NOT_TAGGED",
        "OFFICIAL_NOT_OFFERED_THIS_TERM", "TERM_TAGGED_NOT_ON_CURRENT_LIST",
    ):
        print(f"  {status}: {counts[status]}")
    print(f"Saved: {args.output.resolve()}")
    print("Notes: no course/attribute rows were added or modified in Supabase.")
    print("Discrepancies require review; this list has no catalog-year guarantee.")


if __name__ == "__main__":
    main()
