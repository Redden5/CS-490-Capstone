"""Seed section-level attributes from backend/scripts/sections.csv into Supabase.

Run from the project root:
    python -m backend.scripts.seed_attributes --dry-run
    python -m backend.scripts.seed_attributes

Requires: supabase, python-dotenv
Environment: SUPABASE_URL and SUPABASE_SECRET_KEY in project-root .env
"""

import argparse
import csv
import os
import re
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_CSV = SCRIPT_DIR / "sections.csv"
ATTRIBUTE_PATTERN = re.compile(r"\(([^()]*)\)")
MARKER_PATTERN = re.compile(r"ATTRIBUTES:\s*", re.IGNORECASE)
PAGE_SIZE = 1000
WRITE_BATCH_SIZE = 300


def extract_attributes(notes: str) -> set[str]:
    """Extract the parenthesized attributes following the ATTRIBUTES: marker."""
    match = MARKER_PATTERN.search(notes or "")
    if not match:
        return set()
    attribute_text = notes[match.end():]
    names = {name.strip() for name in ATTRIBUTE_PATTERN.findall(attribute_text)}
    names.discard("")
    if not names:
        raise ValueError(f"Attribute marker found but no attributes could be parsed: {notes!r}")
    return names


def load_csv(path: Path, expected_term_code: str) -> dict[str, set[str]]:
    """Return CRN -> attributes; validate unique CRNs within the term."""
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    sections: dict[str, set[str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"term_code", "crn", "notes"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV missing required columns: {sorted(missing)}")

        for line_number, row in enumerate(reader, start=2):
            term_code = (row.get("term_code") or "").strip()
            if term_code != expected_term_code:
                raise ValueError(
                    f"Row {line_number}: term {term_code!r} does not match {expected_term_code!r}"
                )
            crn = (row.get("crn") or "").strip()
            if not crn:
                raise ValueError(f"Row {line_number}: missing CRN")
            if crn in sections:
                raise ValueError(f"Duplicate CRN {crn} in CSV (row {line_number})")
            sections[crn] = extract_attributes(row.get("notes") or "")

    return sections


def get_term_id(client, term_code: str) -> int:
    result = client.table("term").select("term_id").eq("term_code", term_code).execute()
    rows = result.data or []
    if len(rows) != 1:
        raise ValueError(f"Expected exactly one term with term_code={term_code}; found {len(rows)}")
    return int(rows[0]["term_id"])


def get_section_ids(client, term_id: int) -> dict[str, int]:
    """Fetch every section, with pagination past Supabase's default row limit."""
    section_ids: dict[str, int] = {}
    start = 0
    while True:
        result = (
            client.table("coursesection")
            .select("section_id,crn")
            .eq("term_id", term_id)
            .order("section_id")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        rows = result.data or []
        for row in rows:
            crn = str(row["crn"] or "").strip()
            if not crn:
                raise ValueError(f"Database section {row['section_id']} has no CRN")
            if crn in section_ids:
                raise ValueError(f"Duplicate database CRN: {crn}")
            section_ids[crn] = int(row["section_id"])
        if len(rows) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return section_ids


def chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed section attributes into Supabase")
    parser.add_argument("--dry-run", action="store_true", help="Validate without database writes")
    parser.add_argument("--term", default="202701", help="Academic term code (default: 202701)")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Path to sections CSV")
    args = parser.parse_args()

    sections = load_csv(args.csv, args.term)
    counts = Counter(name for attrs in sections.values() for name in attrs)
    section_count = sum(bool(attrs) for attrs in sections.values())
    link_count = sum(len(attrs) for attrs in sections.values())

    print(f"CSV sections: {len(sections)}")
    print(f"Sections with attributes: {section_count}")
    print(f"Distinct attribute names: {len(counts)}")
    print(f"Section-to-attribute relationships: {link_count}")

    load_dotenv(PROJECT_ROOT / ".env")
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise EnvironmentError("SUPABASE_URL or SUPABASE_SECRET_KEY is missing from .env")

    client = create_client(url, key)
    term_id = get_term_id(client, args.term)
    section_ids = get_section_ids(client, term_id)

    missing = sorted(set(sections) - set(section_ids))
    if missing:
        raise ValueError(
            f"{len(missing)} CSV CRNs have no matching database section for term {args.term}: "
            + ", ".join(missing[:15])
        )
    extra = sorted(set(section_ids) - set(sections))
    if extra:
        print(f"WARNING: {len(extra)} database sections are absent from this CSV.")

    print(f"Resolved all {len(sections)} CSV sections to database section IDs (term_id={term_id}).")
    if args.dry_run:
        print("DRY RUN PASSED: No database writes were made.")
        return

    # Re-running the script preserves existing attribute IDs.
    names = sorted(counts)
    for batch in chunks([{"attribute_name": name} for name in names], WRITE_BATCH_SIZE):
        client.table("attribute").upsert(batch, on_conflict="attribute_name").execute()

    # The unique attribute_name constraint lets us resolve generated IDs.
    result = client.table("attribute").select("attribute_id,attribute_name").execute()
    attribute_ids = {
        row["attribute_name"]: int(row["attribute_id"]) for row in (result.data or [])
    }
    unresolved = set(names) - set(attribute_ids)
    if unresolved:
        raise ValueError(f"Could not resolve attribute IDs for: {sorted(unresolved)}")

    links = [
        {"section_id": section_ids[crn], "attribute_id": attribute_ids[name]}
        for crn, attrs in sorted(sections.items())
        for name in sorted(attrs)
    ]
    for batch in chunks(links, WRITE_BATCH_SIZE):
        client.table("sectionattribute").upsert(
            batch, on_conflict="section_id,attribute_id"
        ).execute()

    print(f"IMPORT COMPLETE: {len(names)} attributes and {len(links)} section-attribute links upserted.")
    print("Note: this is an additive upsert; it does not delete stale links from prior CSV versions.")


if __name__ == "__main__":
    main()
