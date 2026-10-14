"""Seed official course-level Core II attributes into Supabase.

Run from project root:
    python -m backend.scripts.seed_course_attributes --dry-run
    python -m backend.scripts.seed_course_attributes

This script does not infer historical catalog-year applicability from a current
university listing. It never copies section attributes to course attributes.
"""

import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from supabase import create_client

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = PROJECT_ROOT / "backend" / "scripts" / "data" / "core_ii_courses.json"
BATCH_SIZE = 100


def chunks(values: list, batch_size: int = BATCH_SIZE):
    for i in range(0, len(values), batch_size):
        yield values[i : i + batch_size]


def parse_utc_timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError(f"Timestamp must include a UTC offset: {value}")
    return result.astimezone(timezone.utc)


def normalize_course_code(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).upper()


def read_source(path: Path) -> tuple[str, str, list[tuple[str, str]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    source_url = document.get("source_url")
    if not isinstance(source_url, str) or not source_url.startswith("https://"):
        raise ValueError("JSON must contain an HTTPS source_url for the listing.")

    # We are using a current public listing, not a verified historical policy.
    if document.get("catalog_year") is not None:
        raise ValueError(
            "This seeder only handles unassigned catalog years. "
            "Review catalog-year applicability before importing dated records."
        )

    scraped_at = document.get("scraped_at_utc")
    if not isinstance(scraped_at, str):
        raise ValueError("JSON is missing scraped_at_utc.")
    observed_at = parse_utc_timestamp(scraped_at).isoformat()

    categories = document.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ValueError("JSON must have a nonempty categories list.")

    pairs: list[tuple[str, str]] = []
    category_counts: Counter[str] = Counter()
    seen: set[tuple[str, str]] = set()

    for category in categories:
        category_name = category.get("category")
        attribute_name = category.get("attribute_name")
        course_list = category.get("courses")
        if not isinstance(category_name, str) or not category_name:
            raise ValueError("Found a category without a category name.")
        if not isinstance(attribute_name, str) or not attribute_name.strip():
            raise ValueError(f"Missing attribute_name for category {category_name!r}.")
        if not isinstance(course_list, list):
            raise ValueError(f"Missing courses list for {category_name!r}.")

        for course in course_list:
            code = course.get("course_code")
            if not isinstance(code, str) or not code.strip():
                raise ValueError(f"Invalid course_code under {category_name!r}.")
            key = (normalize_course_code(code), attribute_name.strip())
            if key in seen:
                raise ValueError(f"Duplicate course/attribute association in JSON: {key}")
            seen.add(key)
            pairs.append(key)
            category_counts[category_name] += 1

    summary = document.get("summary", {})
    expected_checks = {
        "category_count": len(categories),
        "category_course_links": len(pairs),
        "distinct_course_codes": len({code for code, _ in pairs}),
    }
    for field, actual in expected_checks.items():
        if field in summary and summary[field] != actual:
            raise ValueError(f"JSON summary {field}={summary[field]}, but found {actual}.")
    if "by_category" in summary and summary["by_category"] != dict(category_counts):
        raise ValueError("JSON summary by_category differs from parsed course lists.")

    return source_url, observed_at, pairs


def fetch_id_map(client: Any, table: str, id_field: str, name_field: str,
                 names: set[str]) -> dict[str, int]:
    lookup: dict[str, int] = {}
    for group in chunks(sorted(names)):
        response = (
            client.table(table)
            .select(f"{id_field},{name_field}")
            .in_(name_field, group)
            .execute()
        )
        for item in response.data or []:
            name = item[name_field]
            if name in lookup:
                raise ValueError(f"Duplicate {name_field} in {table}: {name}")
            lookup[name] = item[id_field]
    return lookup


def fetch_existing(client: Any, source_url: str) -> dict[tuple[int, int, str], dict]:
    existing: dict[tuple[int, int, str], dict] = {}
    page_size = 500
    start = 0
    while True:
        response = (
            client.table("courseattribute")
            .select("course_id,attribute_id,source_url,observed_at,catalog_year_id")
            .eq("source_url", source_url)
            .order("course_id")
            .order("attribute_id")
            .range(start, start + page_size - 1)
            .execute()
        )
        records = response.data or []
        for item in records:
            key = (item["course_id"], item["attribute_id"], item["source_url"])
            existing[key] = item
        if len(records) < page_size:
            return existing
        start += page_size


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                        help="Path to scrape_core_ii.py JSON output")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate and preview changes without database writes")
    args = parser.parse_args()

    source_url, observed_at, pairs = read_source(args.input)
    print(f"Source: {source_url}")
    print(f"Observed: {observed_at}")
    print(f"JSON: {len(pairs)} associations, "
          f"{len({code for code, _ in pairs})} courses, "
          f"{len({name for _, name in pairs})} attributes")

    load_dotenv(PROJECT_ROOT / ".env")
    url = os.getenv("SUPABASE_URL")
    secret_key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not secret_key:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_SECRET_KEY in your project .env file.")
    client = create_client(url, secret_key)

    course_ids = fetch_id_map(client, "course", "course_id", "course_code",
                              {code for code, _ in pairs})
    attribute_ids = fetch_id_map(client, "attribute", "attribute_id", "attribute_name",
                                 {name for _, name in pairs})

    missing_courses = sorted({code for code, _ in pairs} - set(course_ids))
    missing_attributes = sorted({name for _, name in pairs} - set(attribute_ids))
    if missing_courses or missing_attributes:
        print(f"Missing courses ({len(missing_courses)}): {missing_courses}")
        print(f"Missing attributes ({len(missing_attributes)}): {missing_attributes}")
        raise RuntimeError("Aborted: unresolved course or attribute mappings. No writes made.")

    existing = fetch_existing(client, source_url)
    print(f"Existing associations for this source: {len(existing)}")

    to_write: list[dict] = []
    inserted = updated = skipped = 0
    expected_keys: set[tuple[int, int, str]] = set()
    new_dt = parse_utc_timestamp(observed_at)

    for code, name in pairs:
        key = (course_ids[code], attribute_ids[name], source_url)
        expected_keys.add(key)
        old = existing.get(key)
        if old is not None and parse_utc_timestamp(old["observed_at"]) >= new_dt:
            skipped += 1
            continue

        row = {
            "course_id": key[0],
            "attribute_id": key[1],
            "source_url": source_url,
            "observed_at": observed_at,
            # Preserve previously verified catalog assignments, if any.
            "catalog_year_id": old["catalog_year_id"] if old else None,
        }
        to_write.append(row)
        if old is None:
            inserted += 1
        else:
            updated += 1

    stale_keys = set(existing) - expected_keys
    if stale_keys:
        print(f"WARNING: {len(stale_keys)} existing associations for this source "
              "are absent from this JSON. Not deleting them automatically.")

    print(f"Planned: insert={inserted}, update={updated}, skip={skipped}")
    if args.dry_run:
        print("DRY RUN: no database writes performed.")
        return

    for batch in chunks(to_write):
        client.table("courseattribute").upsert(
            batch, on_conflict="course_id,attribute_id,source_url"
        ).execute()

    after = fetch_existing(client, source_url)
    missing_after = expected_keys - set(after)
    if missing_after:
        raise RuntimeError(f"Verification failed: {len(missing_after)} associations absent.")

    print(f"Completed: inserted={inserted}, updated={updated}, skipped={skipped}")
    print(f"Verified expected associations: {len(expected_keys)} / {len(pairs)}")
    print(f"Total associations at this source URL: {len(after)}")
    if set(after) - expected_keys:
        print("WARNING: Unexpected/stale source associations remain. Review manually.")


if __name__ == "__main__":
    main()
