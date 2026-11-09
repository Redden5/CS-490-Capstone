"""Validate and seed course-level academic attributes from official Marshall sources.

Run from repository root:
    python -m backend.scripts.seed_course_attributes --dry-run
    python -m backend.scripts.seed_course_attributes --catalog-csv backend/scripts/catalog_attributes.csv --dry-run

The existing JSON mode handles the undated Core II listing. The --catalog-csv
mode handles a dated Courses A-Z catalog export. Both modes preserve the source
URL and never copy semester-only attributes into courseattribute.

No new course or attribute definitions are automatically created and no source
rows are deleted. Catalog-year foreign keys are resolved by matching the dated
CSV's catalog_year value to the existing catalogyear table; undated Core II
listing associations retain NULL catalog_year_id.
"""

import argparse
import csv
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from supabase import create_client

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON = PROJECT_ROOT / "backend" / "scripts" / "data" / "core_ii_courses.json"
BATCH_SIZE = 100
PAGE_SIZE = 500
CATALOG_COLUMNS = {
    "course_code", "attribute_name", "source_url", "catalog_year",
    "catalog_level", "observed_at_utc",
}


def chunks(values: list, size: int = BATCH_SIZE):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def normalize(value: str) -> str:
    return " ".join(value.strip().upper().split())


def timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Observation timestamp is missing timezone: {value}")
    return parsed.astimezone(timezone.utc).isoformat()


def read_core_ii_json(path: Path) -> tuple[list[dict], str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    source_url = data.get("source_url")
    if source_url != "https://mubert.marshall.edu/coreIIcourses.php":
        raise ValueError("Unexpected Core II source URL. Review before seeding.")
    if data.get("catalog_year") is not None:
        raise ValueError("This Core II source has no verified catalog-year applicability.")
    observed_at = timestamp(data["scraped_at_utc"])
    categories = data.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ValueError("Missing Core II categories.")

    records = []
    seen = set()
    category_counts = Counter()
    for category in categories:
        name = category["attribute_name"].strip()
        category_name = category["category"]
        for course in category["courses"]:
            code = normalize(course["course_code"])
            key = (code, name, source_url)
            if key in seen:
                raise ValueError(f"Repeated source association: {key}")
            seen.add(key)
            category_counts[category_name] += 1
            records.append({"course_code": code, "attribute_name": name,
                            "source_url": source_url, "observed_at": observed_at})

    summary = data.get("summary", {})
    checks = {"category_count": len(categories),
              "category_course_links": len(records),
              "distinct_course_codes": len({r['course_code'] for r in records})}
    for key, value in checks.items():
        if key in summary and summary[key] != value:
            raise ValueError(f"Core II summary {key} does not match parsed records.")
    if "by_category" in summary and summary["by_category"] != dict(category_counts):
        raise ValueError("Core II per-category counts do not match JSON summary.")
    return records, "Undated Core II listing; catalog_year_id remains NULL"


def read_catalog_csv(path: Path) -> tuple[list[dict], str, str]:
    records = []
    seen = set()
    years = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = CATALOG_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV missing required columns: {sorted(missing)}")
        for row_number, row in enumerate(reader, start=2):
            code = normalize(row["course_code"])
            name = row["attribute_name"].strip()
            url = row["source_url"].strip()
            year = row["catalog_year"].strip()
            level = row["catalog_level"].strip().lower()
            if not code or not name or not year:
                raise ValueError(f"CSV row {row_number}: missing code, attribute, or year")
            if not re.fullmatch(r"\d{4}-\d{4}", year):
                raise ValueError(f"CSV row {row_number}: unexpected catalog year {year!r}")
            if level not in {"undergraduate", "graduate"}:
                raise ValueError(f"CSV row {row_number}: invalid catalog level {level!r}")
            parsed = urlparse(url)
            if (parsed.scheme != "https" or parsed.netloc != "catalog.marshall.edu"
                    or not parsed.path.startswith(f"/{level}/courses-az/")):
                raise ValueError(f"CSV row {row_number}: invalid source URL {url!r}")
            key = (code, name, url)
            if key in seen:
                raise ValueError(f"CSV row {row_number}: duplicate source association {key}")
            seen.add(key)
            years.add(year)
            records.append({"course_code": code, "attribute_name": name,
                            "source_url": url, "observed_at": timestamp(row["observed_at_utc"])})
    if not records:
        raise ValueError("CSV contains no attribute associations")
    if len(years) != 1:
        raise ValueError(f"Catalog export mixes years: {sorted(years)}")
    year = next(iter(years))
    return records, f"Courses A-Z catalog year {year} (from CSV)", year


def fetch_id_map(client: Any, table: str, id_field: str,
                 name_field: str, names: set[str]) -> dict[str, int]:
    result = {}
    for group in chunks(sorted(names)):
        response = (client.table(table).select(f"{id_field},{name_field}")
                    .in_(name_field, group).execute())
        for item in response.data or []:
            key = item[name_field]
            if key in result:
                raise ValueError(f"Duplicate {name_field} in {table}: {key}")
            result[key] = item[id_field]
    return result


def fetch_existing(client: Any) -> dict[tuple[int, int, str], dict]:
    """Fetch across ALL sources so per-course URLs work without N+1 queries."""
    existing = {}
    start = 0
    while True:
        response = (client.table("courseattribute")
                    .select("course_id,attribute_id,source_url,observed_at,catalog_year_id")
                    .order("course_id").order("attribute_id").order("source_url")
                    .range(start, start + PAGE_SIZE - 1).execute())
        page = response.data or []
        for row in page:
            key = (row["course_id"], row["attribute_id"], row["source_url"])
            existing[key] = row
        if len(page) < PAGE_SIZE:
            return existing
        start += PAGE_SIZE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--catalog-csv", type=Path,
                        help="Catalog attribute CSV produced by scrape_catalog.py")
    choice.add_argument("--input", type=Path, default=DEFAULT_JSON,
                        help="Undated Core II JSON (default mode)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.catalog_csv:
        records, description, catalog_year = read_catalog_csv(args.catalog_csv)
    else:
        records, description = read_core_ii_json(args.input)
        catalog_year = None
    print(f"Source: {description}")
    print(f"Input: {len(records)} associations, "
          f"{len({r['course_code'] for r in records})} courses, "
          f"{len({r['attribute_name'] for r in records})} attributes, "
          f"{len({r['source_url'] for r in records})} source URLs")

    load_dotenv(PROJECT_ROOT / ".env")
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SECRET_KEY in .env")
    client = create_client(url, key)

    # Resolve the year from the CSV label, rather than hardcoding an ID.
    # For the 2026-2027 export the verified database ID is 1.
    catalog_year_id = None
    if catalog_year is not None:
        response = (client.table("catalogyear")
                    .select("catalog_year_id,catalog_label")
                    .eq("catalog_label", catalog_year).execute())
        matches = response.data or []
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected exactly one catalogyear row for {catalog_year!r}; "
                f"found {len(matches)}. No writes made."
            )
        catalog_year_id = matches[0]["catalog_year_id"]
        print(f"Resolved catalog year: {catalog_year} -> catalog_year_id={catalog_year_id}")

    courses = fetch_id_map(client, "course", "course_id", "course_code",
                           {r["course_code"] for r in records})
    attributes = fetch_id_map(client, "attribute", "attribute_id", "attribute_name",
                              {r["attribute_name"] for r in records})
    missing_courses = sorted({r["course_code"] for r in records} - courses.keys())
    missing_attributes = sorted({r["attribute_name"] for r in records} - attributes.keys())
    print(f"Unmatched courses ({len(missing_courses)}): {missing_courses}")
    print(f"Unmatched attributes ({len(missing_attributes)}): {missing_attributes}")
    if missing_courses or missing_attributes:
        if args.dry_run:
            print("DRY RUN: no database writes. Resolve missing mappings before importing.")
            return
        raise RuntimeError("Import blocked: missing IDs. No writes made.")

    existing = fetch_existing(client)
    source_urls = {r["source_url"] for r in records}
    existing_for_sources = {key for key in existing if key[2] in source_urls}
    expected = set()
    to_write = []
    inserted = updated = skipped = 0
    for row in records:
        db_key = (courses[row["course_code"]], attributes[row["attribute_name"]],
                  row["source_url"])
        if db_key in expected:
            raise ValueError(f"Duplicate database mapping: {db_key}")
        expected.add(db_key)
        previous = existing.get(db_key)
        observed_at = row["observed_at"]
        if previous:
            previous_year_id = previous["catalog_year_id"]
            # An existing record for the same course/attribute/source URL must
            # not be silently reassigned to another catalog year. Catalog URLs
            # can be reused across editions, so this also prevents inadvertently
            # overwriting historical classifications in future scrapes.
            if previous_year_id is not None and previous_year_id != catalog_year_id:
                raise RuntimeError(
                    f"Conflicting catalog-year assignment for {db_key}: "
                    f"existing={previous_year_id}, incoming={catalog_year_id}. "
                    "No writes made; review source versioning."
                )
            if catalog_year_id is None and previous_year_id is not None:
                raise RuntimeError(
                    f"Undated Core II source unexpectedly has a catalog year: {db_key}. "
                    "No writes made."
                )
            prior_observed_at = timestamp(previous["observed_at"])
            if prior_observed_at >= observed_at and previous_year_id == catalog_year_id:
                skipped += 1
                continue
            # Backfilling a missing year must not roll an observation timestamp
            # backward if an existing record was observed more recently.
            if prior_observed_at > observed_at:
                observed_at = prior_observed_at
        to_write.append({"course_id": db_key[0], "attribute_id": db_key[1],
                         "source_url": db_key[2], "observed_at": observed_at,
                         "catalog_year_id": catalog_year_id})
        if previous:
            updated += 1
        else:
            inserted += 1
    stale = existing_for_sources - expected
    if stale:
        print(f"WARNING: {len(stale)} associations previously recorded at these URLs "
              "are not in the current input; they will NOT be deleted.")
    print(f"Planned: insert={inserted}, update={updated}, skip={skipped}")
    if args.dry_run:
        print("DRY RUN: no database writes performed.")
        return

    for group in chunks(to_write):
        client.table("courseattribute").upsert(
            group, on_conflict="course_id,attribute_id,source_url").execute()
    after = fetch_existing(client)
    absent = expected - after.keys()
    if absent:
        raise RuntimeError(f"Verification failed: {len(absent)} expected rows absent")
    mismatched_year = [key for key in expected
                       if after[key]["catalog_year_id"] != catalog_year_id]
    if mismatched_year:
        raise RuntimeError(
            f"Verification failed: {len(mismatched_year)} associations have "
            "the wrong catalog_year_id"
        )
    print(f"Completed: inserted={inserted}, updated={updated}, skipped={skipped}")
    print(f"Verified: {len(expected)} / {len(records)} expected associations are present")
    print("Source-attributed associations retained; no existing source records deleted.")


if __name__ == "__main__":
    main()
