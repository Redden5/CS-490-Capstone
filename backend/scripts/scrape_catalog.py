"""Scrape Marshall University's 2026-2027 undergraduate and graduate catalog.

Run from the repository root:
    python -m backend.scripts.scrape_catalog

Use --test to scrape just CS undergraduate and graduate pages.
"""

import argparse
import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from backend.models import models

BASE_URL = "https://catalog.marshall.edu"
CATALOG_PATHS = ("undergraduate", "graduate")
OUTPUT_FILE = Path(__file__).resolve().parent / "courses.csv"
# Parse heading from the text of each courseblock instead of assuming a
# .courseblocktitle element (not all CourseLeaf layouts use that class).
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
METADATA_START = re.compile(
    r"\b(?:Pre-req|Prerequisites?|Concurrent PR|Corequisites?|Co-req|CR/PR|"
    r"Grade Mode|Attributes|Repeatability|Equivalent Course|Restrictions|Cross-listed)\s*:",
    re.IGNORECASE,
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
        # The index also links to "courses-az.pdf". It is a PDF,
        # not a subject page, and should never be scraped as HTML.
        # Subject slugs are simple names such as cs, mth, or a_s.
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
    # CourseLeaf subject pages use courseblock markup, which need not
    # be nested under a container named #content.
    return soup.select(".courseblock")


def parse_subject_page(soup, url):
    parsed = []
    blocks = course_blocks(soup)
    if not blocks:
        raise ValueError("No .courseblock elements found; inspect catalog HTML")

    skipped = 0
    for block in blocks:
        # Some pages use .courseblocktitle, others do not. We only need the
        # displayed text; the course heading ends in 'N Credit hours'.
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

        # After the heading, the catalog puts description text followed by
        # metadata such as 'Pre-req:' and 'Grade Mode:'. Do not mix metadata
        # into the general course description.
        after_heading = block_text[match.end():].strip()
        metadata = METADATA_START.search(after_heading)
        description = after_heading[:metadata.start()].strip() if metadata else after_heading
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
        parsed.append(course)

    # Refuse to call a page successful if any course was skipped;
    # missing even one course would give an incomplete catalog.
    if skipped:
        raise ValueError(
            f"Could not parse {skipped} of {len(blocks)} course blocks; "
            "inspect the WARN messages before importing data"
        )
    return parsed


def scrape_catalog(test=False, subject=None):
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
                    page_courses = parse_subject_page(get_soup(client, url), url)
                    print(f"  [{index}/{len(subjects)}] {url}: {len(page_courses)} courses")
                    for course in page_courses:
                        # Undergraduate version wins if a code appears in both.
                        courses.setdefault(course.course_code, course)
                except (httpx.HTTPError, ValueError) as exc:
                    errors.append((url, str(exc)))
                    print(f"  ERROR: {url}: {exc}")
                time.sleep(0.25)  # Respectful request pacing
    return courses, errors


def main():
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--test", action="store_true", help="Scrape only undergraduate/graduate CS")
    selection.add_argument("--subject", type=str, help="Scrape one subject slug, e.g. a_s or cs")
    args = parser.parse_args()
    courses, errors = scrape_catalog(test=args.test, subject=args.subject)
    if not courses:
        raise SystemExit("No courses parsed; existing courses.csv was NOT overwritten")
    if errors:
        raise SystemExit(f"{len(errors)} page errors; existing courses.csv was NOT overwritten")
    output = OUTPUT_FILE.with_name("catalog_test.csv") if (args.test or args.subject) else OUTPUT_FILE
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(models.Course.model_fields))
        writer.writeheader()
        for code in sorted(courses):
            writer.writerow(courses[code].model_dump())
    print(f"\nSaved {len(courses)} unique courses to {output}")
    print(f"Page errors: {len(errors)}")
    if errors:
        print("Review errors before seeding; this CSV may be incomplete.")
        for url, error in errors:
            print(f"  {url}: {error}")


if __name__ == "__main__":
    main()
