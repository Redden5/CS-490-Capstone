"""Import section and meeting staging CSVs into Marshall Course Advisor Supabase.

Run from the repository root:
    python -m backend.scripts.seed_sections --dry-run
    python -m backend.scripts.seed_sections --term-name "Fall 2026"

Preconditions:
- All course codes in sections.csv already exist in public.course.
- public.term.term_code is UNIQUE.
- public.coursesection has UNIQUE (term_id, crn).
- Primary keys on term, coursesection, sectionmeeting are generated identities.

The upload uses Supabase/PostgREST, which does NOT provide a single transaction
across the several requests. If interrupted, rerun this script to reconcile
sections and replace their meeting rows.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter
from datetime import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

OUTPUT_DIR = Path(__file__).resolve().parent
DAY_NUMBERS = {
    "Monday": 1,
    "Tuesday": 2,
    "Wednesday": 3,
    "Thursday": 4,
    "Friday": 5,
    "Saturday": 6,
    "Sunday": 7,
}
SECTION_REQUIRED = {"term_code", "course_code", "crn", "section_number"}
MEETING_REQUIRED = {"term_code", "crn", "meeting_number", "day_of_week", "start_time", "end_time"}
PAGE_SIZE = 500
SECTION_BATCH_SIZE = 100
MEETING_BATCH_SIZE = 200


class SeedError(Exception):
    """Validation or import failure that should halt the process."""


def read_csv(path: Path, required: set[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise SeedError(f"Missing CSV file: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        if not required.issubset(fields):
            raise SeedError(f"{path.name} missing columns: {sorted(required - fields)}")
        rows = [{k: (v or "").strip() for k, v in row.items()} for row in reader]
    return rows


def optional(value: str | None) -> str | None:
    value = (value or "").strip()
    return None if value in {"", "**", "N/A", "--"} else value


def optional_int(value: str | None, context: str) -> int | None:
    parsed = optional(value)
    if parsed is None:
        return None
    try:
        number = int(parsed)
    except ValueError as exc:
        raise SeedError(f"{context}: invalid integer {parsed!r}") from exc
    if number < 0:
        raise SeedError(f"{context}: negative integer {number}")
    return number


def validate_time(value: str, context: str) -> str | None:
    result = optional(value)
    if result is None:
        return None
    try:
        time.fromisoformat(result)
    except ValueError as exc:
        raise SeedError(f"{context}: invalid ISO time {result!r}") from exc
    return result


def chunks(items: list, chunk_size: int):
    for start in range(0, len(items), chunk_size):
        yield items[start : start + chunk_size]


def validate_staging(sections: list[dict], meetings: list[dict], term_code: str) -> dict[str, list[dict]]:
    if not sections:
        raise SeedError("sections.csv is empty; refusing import")
    seen_crns = set()
    for number, row in enumerate(sections, 2):
        crn = row["crn"]
        code = row["course_code"].upper()
        sec = row["section_number"]
        if row["term_code"] != term_code:
            raise SeedError(f"sections.csv line {number}: unexpected term {row['term_code']}")
        if not re.fullmatch(r"\d{3,8}", crn):
            raise SeedError(f"sections.csv line {number}: invalid CRN {crn!r}")
        if not code or not sec:
            raise SeedError(f"sections.csv line {number}: missing course_code or section_number")
        if crn in seen_crns:
            raise SeedError(f"Duplicate CRN in sections.csv: {crn}")
        seen_crns.add(crn)
        capacity = optional_int(row.get("capacity"), f"CRN {crn} capacity")
        enrolled = optional_int(row.get("enrolled"), f"CRN {crn} enrolled")
        if capacity is not None and enrolled is not None and enrolled > capacity:
            raise SeedError(f"CRN {crn}: enrollment exceeds capacity")
    by_crn: dict[str, list[dict]] = {row["crn"]: [] for row in sections}
    meeting_numbers = set()
    for number, row in enumerate(meetings, 2):
        crn = row["crn"]
        if row["term_code"] != term_code:
            raise SeedError(f"section_meetings.csv line {number}: unexpected term")
        if crn not in by_crn:
            raise SeedError(f"Meeting line {number}: CRN {crn} not in sections.csv")
        if row["day_of_week"] not in DAY_NUMBERS:
            raise SeedError(f"Meeting line {number}: invalid weekday {row['day_of_week']!r}")
        try:
            meeting_number = int(row["meeting_number"])
        except ValueError as exc:
            raise SeedError(f"Meeting line {number}: invalid meeting_number") from exc
        if meeting_number < 1 or (crn, meeting_number) in meeting_numbers:
            raise SeedError(f"Meeting line {number}: duplicate/invalid meeting number for CRN {crn}")
        meeting_numbers.add((crn, meeting_number))
        start = validate_time(row.get("start_time", ""), f"CRN {crn} start_time")
        end = validate_time(row.get("end_time", ""), f"CRN {crn} end_time")
        if bool(start) != bool(end):
            raise SeedError(f"CRN {crn}: start/end times must both be present or absent")
        if start and time.fromisoformat(end) <= time.fromisoformat(start):
            raise SeedError(f"CRN {crn}: meeting end must be later than start")
        by_crn[crn].append(row)
    return by_crn


def all_rows(query_factory, page_size: int = PAGE_SIZE) -> list[dict]:
    """Page explicitly: Supabase often caps an unpaged select at 1,000 rows."""
    collected = []
    offset = 0
    while True:
        response = query_factory().range(offset, offset + page_size - 1).execute()
        page = response.data or []
        collected.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return collected


def get_client() -> Client:
    load_dotenv(Path.cwd() / ".env")
    load_dotenv()  # Also allow an .env beside the backend package.
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SeedError("Set SUPABASE_URL and SUPABASE_SECRET_KEY in .env")
    return create_client(url, key)


def get_course_ids(client: Client) -> dict[str, int]:
    rows = all_rows(lambda: client.table("course").select("course_id,course_code").order("course_id"))
    result: dict[str, int] = {}
    for row in rows:
        code = (row["course_code"] or "").strip().upper()
        if code in result and result[code] != row["course_id"]:
            raise SeedError(f"Duplicate course_code in Supabase: {code}")
        result[code] = row["course_id"]
    print(f"Loaded {len(result)} catalog course IDs from Supabase.")
    return result


def get_or_create_term(client: Client, term_code: str, term_name: str) -> int:
    found = client.table("term").select("term_id,term_name").eq("term_code", term_code).limit(1).execute().data
    if found:
        print(f"Using existing term {term_code}: {found[0]['term_name']} (ID {found[0]['term_id']}).")
        return int(found[0]["term_id"])
    payload = {"term_code": term_code, "term_name": term_name}
    inserted = client.table("term").insert(payload).execute().data
    if not inserted:
        raise SeedError("Term insertion returned no data")
    term_id = int(inserted[0]["term_id"])
    print(f"Created term {term_name} ({term_code}), ID {term_id}.")
    return term_id


def make_section_payload(row: dict[str, str], course_id: int, term_id: int) -> dict[str, Any]:
    return {
        "course_id": course_id,
        "term_id": term_id,
        "crn": row["crn"],
        "section_number": row["section_number"],
        "instructor": optional(row.get("instructor")),
        "campus": optional(row.get("campus")),
        "delivery_mode": optional(row.get("delivery_mode")),
        # An unknown status must not default to OPEN without source confirmation.
        "section_status": (optional(row.get("section_status")) or "UNKNOWN").upper(),
        "capacity": optional_int(row.get("capacity"), f"CRN {row['crn']} capacity"),
        "enrolled": optional_int(row.get("enrolled"), f"CRN {row['crn']} enrolled"),
        "source_url": optional(row.get("source_url")),
    }


def fetch_section_ids(client: Client, term_id: int) -> dict[str, dict]:
    rows = all_rows(
        lambda: client.table("coursesection")
        .select("section_id,course_id,section_number,crn")
        .eq("term_id", term_id)
        .order("section_id")
    )
    by_crn: dict[str, dict] = {}
    for item in rows:
        crn = item.get("crn")
        if crn:
            if crn in by_crn:
                raise SeedError(f"Existing duplicate CRN {crn} in Supabase term ID {term_id}")
            by_crn[crn] = item
    return by_crn


def make_meeting_payload(row: dict[str, str], section_id: int) -> dict[str, Any]:
    meeting_type = optional(row.get("meeting_type"))
    return {
        "section_id": section_id,
        "day_of_week": DAY_NUMBERS[row["day_of_week"]],
        "start_time": optional(row.get("start_time")),
        "end_time": optional(row.get("end_time")),
        "building": optional(row.get("building")),
        "room": optional(row.get("room")),
        "meeting_type": meeting_type or "CLASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Supabase term, sections and meeting rows")
    parser.add_argument("--term", default="202701", help="Schedule term code, default 202701")
    parser.add_argument("--term-name", default="Fall 2026", help="Human-readable term name")
    parser.add_argument("--sections-file", type=Path, default=OUTPUT_DIR / "sections.csv")
    parser.add_argument("--meetings-file", type=Path, default=OUTPUT_DIR / "section_meetings.csv")
    parser.add_argument("--dry-run", action="store_true", help="Validate and check foreign keys, no writes")
    args = parser.parse_args()
    if not re.fullmatch(r"\d{6}", args.term):
        parser.error("--term must be a six-digit code")
    if args.term != "202701" and args.term_name == "Fall 2026":
        parser.error("Set --term-name for a term other than 202701")

    try:
        sections = read_csv(args.sections_file, SECTION_REQUIRED)
        meetings = read_csv(args.meetings_file, MEETING_REQUIRED)
        meetings_by_crn = validate_staging(sections, meetings, args.term)
        print(f"Validated {len(sections)} sections and {len(meetings)} meeting-day records.")
        # Reused section numbers are legitimate when each row has a distinct CRN.
        section_number_counts = Counter(
            (row["course_code"].upper(), row["section_number"])
            for row in sections
        )
        reused = [(key, n) for key, n in section_number_counts.items() if n > 1]
        if reused:
            print(
                f"INFO: {len(reused)} course/section-number combination(s) are "
                "reused by different CRNs; these will be preserved."
            )
            for (course_code, section_number), count in reused[:8]:
                print(f"  {course_code} section {section_number}: {count} CRNs")
        missing_time_crns = {
            crn for crn, rows in meetings_by_crn.items()
            if any(not optional(row.get("start_time")) for row in rows)
        }
        print(f"{len(missing_time_crns)} sections have a meeting day with unknown time (preserved as NULL).")

        client = get_client()
        course_ids = get_course_ids(client)
        unmatched = sorted({row["course_code"].upper() for row in sections} - course_ids.keys())
        if unmatched:
            raise SeedError(
                "Missing course codes in Supabase: " + ", ".join(unmatched)
                + ". Add verified course entries before importing; no changes were made."
            )

        if args.dry_run:
            print("DRY RUN PASSED: all course references resolve; no database writes were made.")
            return 0

        # Preflight existing data before making any writes. A CRN should never
        # silently move to a different course/section in the same term.
        existing_term = client.table("term").select("term_id").eq("term_code", args.term).limit(1).execute().data
        if existing_term:
            existing_rows = all_rows(
                lambda: client.table("coursesection")
                .select("section_id,course_id,section_number,crn")
                .eq("term_id", int(existing_term[0]["term_id"]))
                .order("section_id")
            )
            existing_crns = {}
            for record in existing_rows:
                old_crn = record.get("crn")
                if old_crn:
                    if old_crn in existing_crns:
                        raise SeedError(f"Existing duplicate CRN {old_crn} in term {args.term}")
                    existing_crns[old_crn] = record
            for row in sections:
                record = existing_crns.get(row["crn"])
                if record and (
                    int(record["course_id"]) != course_ids[row["course_code"].upper()]
                    or record["section_number"] != row["section_number"]
                ):
                    raise SeedError(
                        f"CRN {row['crn']} already belongs to another course/section in Supabase. "
                        "Resolve it before importing; no changes were made."
                    )

        term_id = get_or_create_term(client, args.term, args.term_name)
        section_payloads = [make_section_payload(row, course_ids[row["course_code"].upper()], term_id)
                            for row in sections]
        print("Upserting course sections...")
        for index, batch in enumerate(chunks(section_payloads, SECTION_BATCH_SIZE), start=1):
            client.table("coursesection").upsert(
                batch, on_conflict="term_id,crn"
            ).execute()
            if index % 10 == 0 or index * SECTION_BATCH_SIZE >= len(section_payloads):
                print(f"  Processed {min(index * SECTION_BATCH_SIZE, len(section_payloads))}/{len(section_payloads)} sections")

        stored_by_crn = fetch_section_ids(client, term_id)
        requested_crns = {row["crn"] for row in sections}
        if not requested_crns.issubset(stored_by_crn):
            raise SeedError(f"Section IDs missing after upsert: {sorted(requested_crns - stored_by_crn.keys())[:20]}")
        for row in sections:
            actual = stored_by_crn[row["crn"]]
            expected_course = course_ids[row["course_code"].upper()]
            if int(actual["course_id"]) != expected_course or actual["section_number"] != row["section_number"]:
                raise SeedError(f"CRN {row['crn']}: stored section identity does not match CSV")

        # Replace meetings for exactly the sections in this import. This avoids
        # duplicates on reruns; it is not a transaction across HTTP requests.
        print("Replacing meetings for imported sections...")
        processed = 0
        for section_batch in chunks(sections, SECTION_BATCH_SIZE):
            ids = [int(stored_by_crn[row["crn"]]["section_id"]) for row in section_batch]
            client.table("sectionmeeting").delete().in_("section_id", ids).execute()
            meeting_payloads = [
                make_meeting_payload(meeting, int(stored_by_crn[sec["crn"]]["section_id"]))
                for sec in section_batch
                for meeting in meetings_by_crn[sec["crn"]]
            ]
            for meetings_batch in chunks(meeting_payloads, MEETING_BATCH_SIZE):
                client.table("sectionmeeting").insert(meetings_batch).execute()
            processed += len(section_batch)
            if processed % 1000 == 0 or processed == len(sections):
                print(f"  Processed meetings for {processed}/{len(sections)} sections")

        print(f"IMPORT COMPLETE: {len(sections)} sections and {len(meetings)} meeting records for term {args.term}.")
        print("Note: PostgREST calls are not one atomic transaction. Rerun after an interruption.")
        return 0
    except SeedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"DATABASE/NETWORK ERROR: {exc}", file=sys.stderr)
        print("If a write failed midway, rerun the command after fixing it to reconcile meetings.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
