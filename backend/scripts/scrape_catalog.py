"""Scrape Marshall University's 2026-2027 undergraduate and graduate catalog.

Run from repository root:

    python -m backend.scripts.scrape_catalog

Use --test for CS undergraduate and graduate pages, or --subject mth.

Outputs (full run):

    backend/scripts/courses.csv              (existing schema; unchanged)

    backend/scripts/catalog_attributes.csv
    backend/scripts/catalog_prerequisites.csv (raw prerequisite metadata; no rule inference)

No output files are overwritten when scraping reports page errors.

"""

import argparse

import csv

import re

import time

from datetime import datetime, timezone

from pathlib import Path

from urllib.parse import urljoin, urlparse

import httpx

from bs4 import BeautifulSoup

from backend.models import models

BASE_URL = "https://catalog.marshall.edu"

CATALOG_PATHS = ("undergraduate", "graduate")

CATALOG_YEAR = "2026-2027"

OUTPUT_FILE = Path(__file__).resolve().parent / "courses.csv"

ATTRIBUTE_OUTPUT_FILE = OUTPUT_FILE.with_name("catalog_attributes.csv")
PREREQUISITE_OUTPUT_FILE = OUTPUT_FILE.with_name("catalog_prerequisites.csv")

# Parse heading from each courseblock's text (the HTML class for the

# course heading is not consistent across every CourseLeaf page).

COURSE_HEADING = re.compile(

    r"^(?P<subject>[A-Z][A-Z0-9&]{0,7})\s+"

    r"(?P<number>\d{2,4}[A-Z0-9]*(?:-[A-Z0-9]+)?)\s+"

    r"(?P<title>.*?)\s*"

    r"(?P<credits>\d+(?:\.\d+)?(?:\s*(?:-|–|—|to)\s*\d+(?:\.\d+)?)?)"

    r"\s+Credit\s+hours?\b",

    re.IGNORECASE,

)

CREDITS = re.compile(

    r"^(\d+(?:\.\d+)?)(?:\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?))?$",

    re.IGNORECASE,

)

# Named group allows us to identify which metadata field begins, then

# capture its text up until the next metadata field.

METADATA_START = re.compile(

    r"\b(?P<label>Pre-req|Prereq|Pre-requisite|Prerequisites?|Concurrent PR|"
    r"Concurrent Prerequisites?|Corequisites?|Co-requisites?|"

    r"Co-req|CR/PR|Grade Mode|Attributes|Repeatability|Equivalent Course|"

    r"Restrictions|Cross-listed)\s*:",

    re.IGNORECASE,

)

ATTRIBUTE_COLUMNS = (
    "course_code",
    "attribute_name",
    "source_url",
    "catalog_year",
    "catalog_level",
    "observed_at_utc",
)

# One row per catalog metadata field: no attempt to infer logical operators.
# Keeping CR/PR as a distinct label avoids guessing whether it is a prereq
# or corequisite. No row means no metadata was found, NOT verified eligibility.
PREREQUISITE_LABELS = {
    "pre-req", "prereq", "pre-requisite", "prerequisite", "prerequisites",
    "concurrent pr", "concurrent prerequisite", "concurrent prerequisites",
    "corequisite", "corequisites", "co-requisite", "co-requisites",
    "co-req", "cr/pr",
}
PREREQUISITE_COLUMNS = (
    "course_code",
    "field_label",
    "requirement_text",
    "source_url",
    "catalog_year",
    "catalog_level",
    "observed_at_utc",
)

def normalize(text):

    return " ".join(text.replace("\xa0", " ").split())

def get_soup(client, url):

    response = client.get(url)

    response.raise_for_status()

    return BeautifulSoup(response.text, "html.parser")

def discover_subjects(client, level):

    index = f"{BASE_URL}/{level}/courses-az/"

    soup = get_soup(client, index)

    results = {}

    prefix = f"/{level}/courses-az/"

    for a in soup.select("a[href]"):

        absolute = urljoin(index, a["href"])

        parsed = urlparse(absolute)

        path = parsed.path.rstrip("/") + "/"

        if parsed.netloc != urlparse(BASE_URL).netloc or not path.startswith(prefix):

            continue

        slug = path[len(prefix):].strip("/")

        # Skip PDF and other non-subject links.

        if re.fullmatch(r"[a-z0-9_][a-z0-9_-]*", slug, re.IGNORECASE):

            canonical_url = f"{BASE_URL}{prefix}{slug}/"

            results[canonical_url] = normalize(a.get_text(" ", strip=True))

    if not results:

        raise ValueError(f"No subject links found at {index}; inspect the HTML selectors")

    return sorted(results)

def parse_credits(value):

    match = CREDITS.fullmatch(normalize(value))

    if not match:

        return None

    lo = float(match.group(1))

    hi = float(match.group(2) or match.group(1))

    return (lo, hi) if hi >= lo else None

def course_blocks(soup):

    return soup.select(".courseblock")

def parse_metadata(after_heading):

    """Return metadata field labels mapped to their text within one course.

    Extracting by field boundaries avoids capturing later fields such as

    'Grade Mode:' as part of an Attributes value.

    """

    matches = list(METADATA_START.finditer(after_heading))

    fields = {}

    for index, match in enumerate(matches):

        end = matches[index + 1].start() if index + 1 < len(matches) else len(after_heading)

        label = normalize(match.group("label")).casefold()

        value = normalize(after_heading[match.end():end])

        if value:

            fields.setdefault(label, []).append(value)

    return fields

def parse_subject_page(soup, url, attribute_sink=None, prerequisite_sink=None):

    """Parse courses and optional attributes/prerequisite metadata.

    Preserve separate output schemas; neither new field is added to Course.
    """

    parsed = []

    blocks = course_blocks(soup)

    if not blocks:

        raise ValueError("No .courseblock elements found; inspect catalog HTML")

    skipped = 0

    for block in blocks:

        block_text = normalize(block.get_text(" ", strip=True))

        match = COURSE_HEADING.search(block_text)

        if not match:

            print(f"  WARN: cannot parse course heading: {block_text[:140]!r}")

            skipped += 1

            continue

        subject = match.group("subject").upper()

        number = match.group("number").upper()

        title = normalize(match.group("title"))

        credit_text = match.group("credits")

        credits = parse_credits(credit_text)

        if credits is None or not title:

            print(f"  WARN: invalid title/credits: {subject} {number}: {credit_text!r}")

            skipped += 1

            continue

        after_heading = block_text[match.end():].strip()

        metadata_matches = list(METADATA_START.finditer(after_heading))

        description = after_heading[:metadata_matches[0].start()].strip() if metadata_matches else after_heading

        description = description or None

        anchor = block.get("id")

        source_url = f"{url}#{anchor}" if anchor else url

        try:

            course = models.Course(

                course_code=f"{subject} {number}",

                subject_code=subject,

                course_number=number,

                course_title=title,

                description=description,

                min_credit_hours=credits[0],

                max_credit_hours=credits[1],

                active_status=True,

                source_url=source_url,

            )

        except Exception as exc:

            print(f"  WARN: invalid course {subject} {number}: {exc}")

            skipped += 1

            continue

        if attribute_sink is not None or prerequisite_sink is not None:
            fields = parse_metadata(after_heading)

            if attribute_sink is not None:
                attributes = []
                for value in fields.get("attributes", []):
                    for entry in value.split(","):
                        label = normalize(entry)
                        if label and label not in attributes:
                            attributes.append(label)
                attribute_sink[course.course_code] = attributes

            if prerequisite_sink is not None:
                # Preserve distinct original metadata fields and their full
                # unparsed text, including grades, OR/AND, and concurrency.
                prerequisite_sink[course.course_code] = [
                    {"field_label": label, "requirement_text": value}
                    for label, values in fields.items()
                    if label in PREREQUISITE_LABELS
                    for value in values
                ]

        parsed.append(course)

    if skipped:

        raise ValueError(

            f"Could not parse {skipped} of {len(blocks)} course blocks; "

            "inspect the WARN messages before importing data"

        )

    return parsed

def scrape_catalog(test=False, subject=None, attribute_sink=None, prerequisite_sink=None):

    """Return (courses, errors); optionally collect metadata in sinks."""

    courses = {}

    errors = []

    with httpx.Client(

        timeout=45.0,

        follow_redirects=True,

        headers={"User-Agent": "Marshall Course Advisor Academic Data Scraper (student capstone)"},

    ) as client:

        for level in CATALOG_PATHS:

            try:

                subjects = discover_subjects(client, level)

            except (httpx.HTTPError, ValueError) as exc:

                errors.append((level, str(exc)))

                print(f"ERROR: Failed to discover {level} subjects: {exc}")

                continue

            if test:

                subjects = [url for url in subjects if urlparse(url).path.endswith("/cs/")]

                if not subjects:

                    errors.append((level, "CS subject not found"))

                    continue

            elif subject:

                subjects = [url for url in subjects if urlparse(url).path.endswith(f"/{subject.lower()}/")]

            print(f"{level}: {len(subjects)} subject pages")

            for index, url in enumerate(subjects, start=1):

                try:

                    page_attributes = {}
                    page_prerequisites = {}
                    soup = get_soup(client, url)
                    page_courses = parse_subject_page(
                        soup, url,
                        attribute_sink=page_attributes if attribute_sink is not None else None,
                        prerequisite_sink=page_prerequisites if prerequisite_sink is not None else None,
                    )

                    print(f"  [{index}/{len(subjects)}] {url}: {len(page_courses)} courses")

                    for course in page_courses:

                        # Keep original behavior: undergraduate record wins.

                        if course.course_code not in courses:

                            courses[course.course_code] = course

                            if attribute_sink is not None:

                                attribute_sink[course.course_code] = {
                                    "attributes": page_attributes.get(course.course_code, []),
                                    "source_url": course.source_url,
                                    "catalog_level": level,
                                }
                            if prerequisite_sink is not None:
                                prerequisite_sink[course.course_code] = {
                                    "fields": page_prerequisites.get(course.course_code, []),
                                    "source_url": course.source_url,
                                    "catalog_level": level,
                                }

                except (httpx.HTTPError, ValueError) as exc:

                    errors.append((url, str(exc)))

                    print(f"  ERROR: {url}: {exc}")

                time.sleep(0.25)

    return courses, errors

def main():

    parser = argparse.ArgumentParser()

    selection = parser.add_mutually_exclusive_group()

    selection.add_argument("--test", action="store_true", help="Scrape undergraduate/graduate CS only")

    selection.add_argument("--subject", type=str, help="Scrape one subject slug, e.g. a_s or mth")

    args = parser.parse_args()

    attributes_by_course = {}
    prerequisites_by_course = {}
    courses, errors = scrape_catalog(
        test=args.test,
        subject=args.subject,
        attribute_sink=attributes_by_course,
        prerequisite_sink=prerequisites_by_course,
    )

    if not courses:

        raise SystemExit("No courses parsed; existing CSV files were NOT overwritten")

    if errors:

        raise SystemExit(f"{len(errors)} page errors; existing CSV files were NOT overwritten")

    testing = args.test or args.subject

    output = OUTPUT_FILE.with_name("catalog_test.csv") if testing else OUTPUT_FILE

    attribute_output = (
        ATTRIBUTE_OUTPUT_FILE.with_name("catalog_attributes_test.csv")
        if testing else ATTRIBUTE_OUTPUT_FILE
    )
    prerequisite_output = (
        PREREQUISITE_OUTPUT_FILE.with_name("catalog_prerequisites_test.csv")
        if testing else PREREQUISITE_OUTPUT_FILE
    )

    observed_at = datetime.now(timezone.utc).isoformat()

    # Existing course CSV format stays unchanged for seed_supabase.py.

    with output.open("w", newline="", encoding="utf-8") as handle:

        writer = csv.DictWriter(handle, fieldnames=list(models.Course.model_fields))

        writer.writeheader()

        for code in sorted(courses):

            writer.writerow(courses[code].model_dump())

    count = 0

    with attribute_output.open("w", newline="", encoding="utf-8") as handle:

        writer = csv.DictWriter(handle, fieldnames=ATTRIBUTE_COLUMNS)

        writer.writeheader()

        for code, info in sorted(attributes_by_course.items()):

            for attr in info["attributes"]:

                writer.writerow({

                    "course_code": code,

                    "attribute_name": attr,

                    "source_url": info["source_url"],

                    "catalog_year": CATALOG_YEAR,

                    "catalog_level": info["catalog_level"],

                    "observed_at_utc": observed_at,

                })

                count += 1

    prerequisite_count = 0
    with prerequisite_output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PREREQUISITE_COLUMNS)
        writer.writeheader()
        for code, info in sorted(prerequisites_by_course.items()):
            for field in info["fields"]:
                writer.writerow({
                    "course_code": code,
                    "field_label": field["field_label"],
                    "requirement_text": field["requirement_text"],
                    "source_url": info["source_url"],
                    "catalog_year": CATALOG_YEAR,
                    "catalog_level": info["catalog_level"],
                    "observed_at_utc": observed_at,
                })
                prerequisite_count += 1

    distinct_attrs = {x for info in attributes_by_course.values() for x in info["attributes"]}

    courses_with_attrs = sum(bool(info["attributes"]) for info in attributes_by_course.values())

    print(f"\nSaved {len(courses)} unique courses to {output}")

    print(f"Saved {count} course-attribute associations to {attribute_output}")
    print(f"Saved {prerequisite_count} raw prerequisite metadata fields to {prerequisite_output}")
    print(f"Courses with prerequisite metadata: {sum(bool(info['fields']) for info in prerequisites_by_course.values())}")

    print(f"Courses with attributes: {courses_with_attrs}")

    print(f"Distinct attribute names: {len(distinct_attrs)}")

    print("No database writes performed. Review extracted prerequisite text before parsing or seeding.")

if __name__ == "__main__":

    main()
