"""Extract Marshall University's public Core II eligible-course listing.

Run from the repository root:
    python -m backend.scripts.scrape_core_ii

Requires: httpx, beautifulsoup4

This is an extraction of the CURRENT public webpage, not a verified
historical/catalog-year degree-audit dataset. No database writes occur.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

SOURCE_URL = "https://mubert.marshall.edu/coreIIcourses.php"
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent / "data" / "core_ii_courses.json"
)

# Map the headings on the official Core II page to the attribute names
# already observed in our sectionattribute dataset. We will verify these
# names against public.attribute before any future database import.
CATEGORY_TO_ATTRIBUTE = {
    "Composition": "Composition Core II",
    "Communications": "Core II Communication Studies",
    "Fine Arts": "Core II Fine Arts",
    "Humanities": "Core II Humanities",
    "Math": "Core II Mathematics",
    "Natural/Physical Science": "Core II Natural Sciences",
    "Social Science": "Core II Social Sciences",
}

CATEGORY_HEADING_RE = re.compile(
    r"^(Composition|Communications|Fine Arts|Humanities|Math|"
    r"Natural\s*/\s*Physical Science|Social Science)\s*"
    r"\(\s*(\d+)\s+credit\s+hours?\s*\)\s*:?$",
    re.IGNORECASE,
)


def clean_text(text: str) -> str:
    """Collapse Unicode whitespace without removing meaningful punctuation."""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_listing(html: str, url: str = SOURCE_URL) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    by_category = {
        category: {"category": category, "attribute_name": attribute,
                   "listed_category_credits": None, "courses": []}
        for category, attribute in CATEGORY_TO_ATTRIBUTE.items()
    }

    current_category = None
    seen = set()
    ungrouped_links = []
    total_course_links = 0

    # BeautifulSoup's descendant sequence is in DOCUMENT order. This is
    # deliberate: the page has irregular/nested <li> markup, so relying on
    # an individual <li>'s parent or sibling relationships is unsafe.
    for node in soup.descendants:
        if isinstance(node, NavigableString):
            text = clean_text(str(node))
            match = CATEGORY_HEADING_RE.fullmatch(text)
            if match:
                found_label = re.sub(r"\s*/\s*", "/", match.group(1))
                current_category = next(
                    (c for c in CATEGORY_TO_ATTRIBUTE
                     if c.casefold() == found_label.casefold()),
                    None,
                )
                if current_category is not None:
                    by_category[current_category]["listed_category_credits"] = int(match.group(2))
            continue

        if not isinstance(node, Tag) or node.name != "a":
            continue

        href = node.get("href", "")
        target = urlparse(urljoin(url, href))
        if not target.path.lower().endswith("/coursedescript.php"):
            continue

        params = parse_qs(target.query)
        subject = params.get("subj", [""])[0].strip().upper()
        course_number = params.get("crse", [""])[0].strip().upper()
        if not subject or not course_number:
            ungrouped_links.append({"reason": "missing_subject_or_number", "href": href})
            continue

        total_course_links += 1
        code = f"{subject} {course_number}"
        title = clean_text(node.get_text(" ", strip=True))
        course = {
            "course_code": code,
            "course_title": title,
            "course_url": urljoin(url, href),
        }
        if current_category is None:
            ungrouped_links.append({"reason": "no_active_category", **course})
            continue

        key = (current_category, code)
        if key not in seen:
            by_category[current_category]["courses"].append(course)
            seen.add(key)

    categories = list(by_category.values())
    missing = [c["category"] for c in categories if c["listed_category_credits"] is None]
    empty = [c["category"] for c in categories if not c["courses"]]
    if missing or empty or ungrouped_links:
        raise ValueError(
            "Unexpected Core II page structure; declining to save incomplete data. "
            f"Missing headings: {missing}; empty categories: {empty}; "
            f"uncategorized course links: {ungrouped_links[:5]} "
            f"(total={len(ungrouped_links)}). Check the live page HTML."
        )

    all_entries = [
        (cat["category"], course["course_code"])
        for cat in categories for course in cat["courses"]
    ]
    unique_codes = {code for _, code in all_entries}

    return {
        "source_url": url,
        "scraped_at_utc": datetime.now(timezone.utc).isoformat(),
        "stage": "unverified_current_core_ii_listing",
        "catalog_year": None,
        "notes": [
            "This source does not specify historical eligibility or a catalog-year effective period.",
            "Do not automatically apply this current list to earlier completed courses or approve degree-audit requirements.",
            "An individual course can occur in more than one Core II category.",
            "Asterisk symbols on the source denote online availability, not Core II eligibility; they are not imported as attributes.",
        ],
        "summary": {
            "category_count": len(categories),
            "category_course_links": len(all_entries),
            "distinct_course_codes": len(unique_codes),
            "source_course_links_seen": total_course_links,
            "by_category": {c["category"]: len(c["courses"]) for c in categories},
        },
        "categories": categories,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=SOURCE_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    with httpx.Client(follow_redirects=True, timeout=30) as client:
        response = client.get(
            args.url,
            headers={"User-Agent": "CourseAdvisorAcademicDataResearch/1.0"},
        )
        response.raise_for_status()

    data = parse_listing(response.text, url=str(response.url))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    summary = data["summary"]
    print("Source:", data["source_url"])
    print("Stage:", data["stage"])
    print("Categories:", summary["category_count"])
    print("Category-course mappings:", summary["category_course_links"])
    print("Distinct courses:", summary["distinct_course_codes"])
    print("Course links seen:", summary["source_course_links_seen"])
    for category, count in summary["by_category"].items():
        print(f"  {category}: {count}")
    print("Saved:", args.output.resolve())


if __name__ == "__main__":
    main()
