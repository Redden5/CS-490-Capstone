"""Extract Marshall University degree requirement tables into reviewable JSON.

Stage 1 only: no Supabase writes and no degree-rule interpretation.

From the project root:
    python -m backend.scripts.scrape_degrees
    python -m backend.scripts.scrape_degrees --url https://catalog.marshall.edu/undergraduate/.../

Requires: httpx, beautifulsoup4
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag


DEFAULT_URL = (
    "https://catalog.marshall.edu/undergraduate/programs-az/"
    "engineering-computer-sciences/computer-science/computer-science-bs/"
)
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "data" / "degree_requirements"
COURSE_CODE_RE = re.compile(r"\b([A-Z][A-Z&/]{1,10})\s+(\d{2,4}[A-Z]{0,3})\b")
SELECTION_RE = re.compile(r"\b(select|choose|complete|pick)\b.*\b(from|following|one of|two of|three of)\b", re.I)
CATALOG_YEAR_RE = re.compile(r"\bCatalog\s+(20\d\d)[-\u2013\u2014](20\d\d)\b", re.I)


def tidy(value: str) -> str:
    """Normalize nonbreaking spaces and whitespace without dropping text."""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def parse_number(text: str) -> int | float | None:
    """Only parse a single explicit credit number; preserve ranges as text."""
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return None
    val = float(text)
    return int(val) if val.is_integer() else val


def parse_cell(cell: Tag, base_url: str) -> dict:
    return {
        "text": tidy(cell.get_text(" ", strip=True)),
        "classes": cell.get("class", []),
        "colspan": cell.get("colspan"),
        "rowspan": cell.get("rowspan"),
        "links": [
            {"text": tidy(a.get_text(" ", strip=True)),
             "url": urljoin(base_url, a.get("href", ""))}
            for a in cell.select("a[href]")
        ],
    }


def extract_row(tr: Tag, table_index: int, row_index: int, url: str) -> dict:
    direct_cells = tr.find_all(["th", "td"], recursive=False)
    cells = [parse_cell(cell, url) for cell in direct_cells]
    classes = tr.get("class", [])
    code_cell = next((c for c in cells if "codecol" in c["classes"]), None)
    hour_cell = next((c for c in cells if "hourscol" in c["classes"]), None)
    hour_text = hour_cell["text"] if hour_cell else ""
    combined_text = tidy(" ".join(c["text"] for c in cells))

    course_codes: list[str] = []
    if code_cell is not None:
        # Anchor text is the best evidence of each course in a combined row.
        for link in code_cell["links"]:
            match = COURSE_CODE_RE.fullmatch(link["text"])
            if match:
                code = f"{match.group(1)} {match.group(2)}"
                if code not in course_codes:
                    course_codes.append(code)
        # Fallback if some course pages omit individual links.
        if not course_codes:
            for match in COURSE_CODE_RE.finditer(code_cell["text"]):
                code = f"{match.group(1)} {match.group(2)}"
                if code not in course_codes:
                    course_codes.append(code)

    if any(c.name == "th" for c in direct_cells):
        kind = "column_header"
    elif "areaheader" in classes or "areasubheader" in classes:
        kind = "section_header"
    elif code_cell is not None:
        kind = "course_row"
    elif SELECTION_RE.search(combined_text):
        kind = "selection_instruction"
    elif hour_text:
        kind = "credit_requirement"
    else:
        kind = "text_or_header"

    review_flags = []
    if code_cell is not None and not course_codes:
        review_flags.append("course_code_unresolved")
    if len(course_codes) > 1:
        review_flags.append("multiple_courses_in_one_row")
    if hour_text and parse_number(hour_text) is None:
        review_flags.append("non_numeric_credit_expression")
    if kind in {"credit_requirement", "text_or_header"}:
        review_flags.append("requires_rule_mapping")
    if kind == "selection_instruction":
        review_flags.append("selection_options_need_grouping")
    if re.search(r"\b(or above|minimum grade|at least|with lab)\b", combined_text, re.I):
        review_flags.append("special_qualification_text")

    return {
        "table_index": table_index,
        "row_index": row_index,
        "kind": kind,
        "row_classes": classes,
        "text": combined_text,
        "code_expression": code_cell["text"] if code_cell else None,
        "course_codes": course_codes,
        "credit_text": hour_text,
        "credit_hours": parse_number(hour_text),
        "cells": cells,
        "review_flags": review_flags,
    }


def extract_degree(html: str, url: str, requested_catalog_year: str | None = None) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.select("table.sc_courselist")
    if not tables:
        raise ValueError("No table.sc_courselist found; cannot safely extract requirements")

    title_node = soup.select_one("h1")
    title = tidy(title_node.get_text(" ", strip=True)) if title_node else ""
    if not title and soup.title:
        title = tidy(soup.title.get_text(" ", strip=True)).split(" | ")[0]

    page_text = soup.get_text(" ", strip=True)
    year_match = CATALOG_YEAR_RE.search(page_text)
    detected_year = f"{year_match.group(1)}-{year_match.group(2)}" if year_match else None
    if requested_catalog_year and detected_year and requested_catalog_year != detected_year:
        raise ValueError(
            f"Catalog year mismatch: requested {requested_catalog_year}, page says {detected_year}"
        )

    rows = [
        extract_row(row, table_idx, row_idx, url)
        for table_idx, table in enumerate(tables)
        for row_idx, row in enumerate(table.find_all("tr"))
    ]
    kind_counts = Counter(row["kind"] for row in rows)
    course_codes = sorted({code for row in rows for code in row["course_codes"]})

    return {
        "program_title": title,
        "catalog_year": requested_catalog_year or detected_year,
        "source_url": url,
        "scraped_at_utc": datetime.now(timezone.utc).isoformat(),
        "stage": "raw_requirements_extraction_only",
        "summary": {
            "requirement_tables": len(tables),
            "total_rows": len(rows),
            "row_types": dict(sorted(kind_counts.items())),
            "distinct_linked_course_codes": len(course_codes),
            "rows_with_review_flags": sum(bool(r["review_flags"]) for r in rows),
        },
        "rows": rows,
    }


def validate_catalog_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "catalog.marshall.edu":
        raise ValueError("--url must be an HTTPS URL on catalog.marshall.edu")


def filename_for_url(url: str) -> str:
    final_segment = urlparse(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"[^a-z0-9]+", "_", final_segment.lower()).strip("_")
    if not slug:
        raise ValueError("Cannot derive a program name from this URL")
    return f"{slug}.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", action="append", help="Catalog program URL (repeatable)")
    parser.add_argument("--catalog-year", default=None, help="Expected catalog year, e.g. 2026-2027")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    urls = args.url or [DEFAULT_URL]
    args.output_dir.mkdir(parents=True, exist_ok=True)

    try:
        with httpx.Client(follow_redirects=True, timeout=30, headers={"User-Agent": "CourseAdvisorCapstone/1.0 (catalog requirement research)"}) as client:
            for url in urls:
                validate_catalog_url(url)
                response = client.get(url)
                response.raise_for_status()
                validate_catalog_url(str(response.url))
                result = extract_degree(response.text, str(response.url), args.catalog_year)
                output = args.output_dir / filename_for_url(url)
                output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                summary = result["summary"]
                print(f"Program: {result['program_title']}")
                print(f"Catalog year: {result['catalog_year'] or 'not detected'}")
                print(f"Requirements tables: {summary['requirement_tables']}")
                print(f"Rows extracted: {summary['total_rows']}")
                print(f"Unique referenced courses: {summary['distinct_linked_course_codes']}")
                print(f"Row types: {summary['row_types']}")
                print(f"Rows flagged for review: {summary['rows_with_review_flags']}")
                print(f"Saved: {output}\n")
    except (httpx.HTTPError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
