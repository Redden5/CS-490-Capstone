"""Scrape Marshall University's public Schedule of Courses into staging CSVs.

Run from the repository root:
    python -m backend.scripts.scrape_sections --test
    python -m backend.scripts.scrape_sections --subject CS --level U
    python -m backend.scripts.scrape_sections --term 202701

The script does NOT alter the catalog courses.csv or write to Supabase.
It emits sections.csv and section_meetings.csv (or *_test.csv for tests).
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

BASE_URL = "https://mubert.marshall.edu"
SCHEDULE_URL = f"{BASE_URL}/scheduleofcourses.php"
OUTPUT_DIR = Path(__file__).resolve().parent
DEFAULT_TERM = "202701"  # Fall 2026
CAMPUSES = "|1|2|3|E|H|T|"
TERM_PARTS = "|1|2|3|"

SECTION_FIELDS = [
    "term_code", "course_code", "crn", "section_number",
    "min_credit_hours", "max_credit_hours", "instructor",
    "delivery_mode", "campus", "section_status", "capacity", "enrolled",
    "location", "notes", "source_url",
]
MEETING_FIELDS = [
    "term_code", "crn", "meeting_number", "day_of_week",
    "start_time", "end_time", "location", "building", "room", "meeting_type",
]
DAY_MAP = {
    "M": "Monday", "T": "Tuesday", "W": "Wednesday", "R": "Thursday",
    "F": "Friday", "S": "Saturday", "U": "Sunday",
}
COURSE_HEADING = re.compile(
    r"^([A-Z][A-Z0-9&]*)\s+([0-9][A-Z0-9-]*)\s*[-\u2013]\s*",
    re.IGNORECASE,
)
TIME_RANGE = re.compile(
    r"(\d{1,2}):(\d{2})\s*([ap])m?\s*[-\u2013]\s*"
    r"(\d{1,2}):(\d{2})\s*([ap])m?\b",
    re.IGNORECASE,
)
CREDIT_RANGE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(?:to|-|\u2013)\s*(\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)
SINGLE_CREDIT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


class ScheduleParseError(ValueError):
    pass


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def cell_lines(cell: Tag) -> list[str]:
    """Keep <br> separated values separate (critical for multiple meeting times)."""
    fragments = re.split(r"<br\b[^>]*>", cell.decode_contents(), flags=re.I)
    lines = [normalize(BeautifulSoup(part, "html.parser").get_text(" ", strip=True))
             for part in fragments]
    return [line for line in lines if line and line != "\xa0"]


def cell_text(cell: Tag) -> str:
    return " ; ".join(cell_lines(cell))


def parse_credits(raw: str) -> tuple[str, str]:
    match = CREDIT_RANGE.fullmatch(raw)
    if match:
        return match.group(1), match.group(2)
    match = SINGLE_CREDIT.fullmatch(raw)
    if match:
        return match.group(1), match.group(1)
    return "", ""  # Preserve the section; warn for unrecognized credit formatting.


def clock_time(hour_text: str, minute_text: str, suffix: str) -> str:
    hour, minute = int(hour_text), int(minute_text)
    if not 1 <= hour <= 12 or not 0 <= minute <= 59:
        raise ScheduleParseError(f"Invalid clock time: {hour_text}:{minute_text}{suffix}")
    hour = hour % 12 + (12 if suffix.lower() == "p" else 0)
    return f"{hour:02d}:{minute:02d}:00"


def parse_time_range(raw: str) -> tuple[str, str] | None:
    match = TIME_RANGE.fullmatch(normalize(raw))
    if not match:
        return None
    a, b, c, d, e, f = match.groups()
    start, end = clock_time(a, b, c), clock_time(d, e, f)
    if end <= start:
        raise ScheduleParseError(f"Time range ends before it starts: {raw!r}")
    return start, end


def parse_days(raw: str) -> list[str]:
    compact = re.sub(r"[\s,/-]", "", raw.upper())
    if not compact or compact in {"TBA", "ARR"}:
        return []
    if any(ch not in DAY_MAP for ch in compact):
        raise ScheduleParseError(f"Unrecognized days: {raw!r}")
    return list(dict.fromkeys(DAY_MAP[ch] for ch in compact))


def clean_placeholder(value: str) -> str:
    """The schedule sometimes displays ** instead of an actual field value."""
    value = normalize(value)
    return "" if value in {"**", "*", "--", "-", "N/A"} else value


def split_location(location: str) -> tuple[str, str, str]:
    """Extract only confidently identifiable physical rooms or online meeting types."""
    location = clean_placeholder(location)
    if location.upper() in {"VIRTUAL", "ONLINE", "ONLINE ASYNC", "WEB"}:
        return "", "", "online"
    if location.upper() in {"TBA", "HTN TBA", "ARR", ""}:
        return "", "", ""
    match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*)\s+([A-Za-z0-9_-]+)", location)
    if match:
        return match.group(1), match.group(2), ""
    return "", "", ""  # Preserve original location in the staging CSV.


def classify_delivery(location: str, notes: str, has_meetings: bool) -> str:
    text = f"{location} {notes}".upper()
    if "HYFLEX" in text:
        return "hyflex"
    if "HYBRID" in text or "BLENDED" in text:
        return "hybrid"
    if "ASYNCHRONOUS" in text or "ONLINE ASYNC" in text or location.upper() == "WEB":
        return "online_async"
    if "SYNCHRONOUS" in text or "VIRTUAL" in text or "ONLINE SYNC" in text:
        return "online_sync"
    if has_meetings:
        return "in_person"  # Can be revised if a published note specifies a modality.
    if "ONLINE" in text:
        return "online_unspecified"
    return "tba"


def locate_course_table(heading: Tag) -> Tag:
    next_element = heading.find_next(["table", "h4"])
    if next_element is None or next_element.name != "table":
        raise ScheduleParseError("Course heading has no following schedule table")
    return next_element


def header_columns(table: Tag) -> dict[str, int]:
    for tr in table.find_all("tr"):
        labels = [normalize(c.get_text(" ", strip=True)).upper()
                  for c in tr.find_all(["th", "td"], recursive=False)]
        if "CRN" in labels and "SEC" in labels:
            mapping = {key: index for index, key in enumerate(labels)}
            required = ("CRN", "SEC", "CREDITS", "DAYS", "TIME", "LOCATION")
            if not all(field in mapping for field in required):
                raise ScheduleParseError(f"Missing required table columns: {labels}")
            return mapping
    raise ScheduleParseError("No CRN/SEC header found in course table")


def column(cells: list[Tag], mapping: dict[str, int], name: str) -> Tag | None:
    index = mapping.get(name)
    return cells[index] if index is not None and index < len(cells) else None


def get_lines(cells: list[Tag], columns: dict[str, int], key: str) -> list[str]:
    cell = column(cells, columns, key)
    return cell_lines(cell) if cell else []


def create_meetings(term: str, crn: str, days: list[str], times: list[str],
                    locations: list[str]) -> list[dict[str, str | int]]:
    # No scheduled meetings for asynchronous, arranged, or TBA classes.
    if not days and not times:
        return []
    if days and not times:
        # The source specifies weekdays but omits the clock time. Preserve the
        # weekdays with NULL times rather than dropping the meeting or guessing.
        times = [""] * len(days)
        print(f"  WARNING: CRN {crn}: days {days!r} have no meeting times; preserving NULL times")
    elif not days or len(days) != len(times):
        raise ScheduleParseError(
            f"CRN {crn}: mismatched meeting patterns: days={days!r}, times={times!r}"
        )
    if len(locations) not in (0, 1, len(days)):
        raise ScheduleParseError(
            f"CRN {crn}: {len(locations)} locations for {len(days)} meeting patterns"
        )

    records = []
    number = 0
    for i, (day_string, time_string) in enumerate(zip(days, times)):
        time_pair = parse_time_range(time_string) if time_string else None
        if time_string and time_pair is None:
            raise ScheduleParseError(f"CRN {crn}: unexpected time {time_string!r}")
        day_names = parse_days(day_string)
        if not day_names:
            raise ScheduleParseError(f"CRN {crn}: unexpected meeting days {day_string!r}")
        location = (locations[i] if len(locations) == len(days)
                    else locations[0] if locations else "")
        building, room, meeting_type = split_location(location)
        for name in day_names:
            number += 1
            records.append({
                "term_code": term,
                "crn": crn,
                "meeting_number": number,
                "day_of_week": name,
                "start_time": time_pair[0] if time_pair else "",
                "end_time": time_pair[1] if time_pair else "",
                "location": location,
                "building": building,
                "room": room,
                "meeting_type": meeting_type,
            })
    return records


def parse_schedule(html: str, source_url: str, term: str) -> tuple[dict[str, dict], dict[str, list[dict]], int]:
    soup = BeautifulSoup(html, "html.parser")
    sections: dict[str, dict] = {}
    meetings: dict[str, list[dict]] = {}
    matched_headings = 0

    for heading in soup.find_all("h4"):
        match = COURSE_HEADING.match(normalize(heading.get_text(" ", strip=True)))
        if not match:
            continue
        matched_headings += 1
        course_code = f"{match.group(1).upper()} {match.group(2).upper()}"
        table = locate_course_table(heading)
        columns = header_columns(table)
        last_crn: str | None = None
        heading_rows = 0

        for tr in table.find_all("tr"):
            cells = tr.find_all("td", recursive=False)
            if not cells:
                continue
            crn_cell = column(cells, columns, "CRN")
            crn = normalize(crn_cell.get_text(" ", strip=True)) if crn_cell else ""
            if not re.fullmatch(r"\d{3,8}", crn):
                # Following rows sometimes contain notes, attributes, or restrictions.
                if last_crn and (not crn or crn in {"\xa0", "-"}):
                    notes = normalize(tr.get_text(" ", strip=True))
                    if notes and notes.upper() not in {"CRN", "ATTRIBUTES:"}:
                        previous = sections[last_crn]["notes"]
                        sections[last_crn]["notes"] = (previous + " " + notes).strip()
                continue

            if crn in sections:
                raise ScheduleParseError(f"Duplicate CRN {crn} in one schedule page")

            sec = get_lines(cells, columns, "SEC")
            credits = get_lines(cells, columns, "CREDITS")
            days = get_lines(cells, columns, "DAYS")
            times = get_lines(cells, columns, "TIME")
            locations = get_lines(cells, columns, "LOCATION")
            instructors = get_lines(cells, columns, "INSTRUCTOR(S)")

            if not sec or not clean_placeholder(sec[0]):
                raise ScheduleParseError(f"CRN {crn} has no section number")
            min_credits, max_credits = parse_credits(credits[0] if credits else "")
            # Avoid storing public email addresses in our course-section dataset.
            instructor = " ; ".join(
                normalize(re.sub(r"\([^)]*@[^)]*\)", "", name))
                for name in instructors
            )
            course_meetings = create_meetings(term, crn, days, times, locations)
            sections[crn] = {
                "term_code": term,
                "course_code": course_code,
                "crn": crn,
                "section_number": clean_placeholder(sec[0]),
                "min_credit_hours": min_credits,
                "max_credit_hours": max_credits,
                "instructor": instructor,
                "delivery_mode": "",  # Inferred after optional notes/attributes rows.
                "campus": "",         # Not explicitly identified per section on this page.
                "section_status": "unknown", # Required DB field; status is not published reliably.
                "capacity": "",       # Not exposed by the page.
                "enrolled": "",       # Not exposed by the page.
                "location": " ; ".join(clean_placeholder(x) for x in locations),
                "notes": "",
                "source_url": source_url,
            }
            meetings[crn] = course_meetings
            last_crn = crn
            heading_rows += 1

        if heading_rows == 0:
            raise ScheduleParseError(f"{course_code}: course heading found but no section rows parsed")

    if matched_headings == 0:
        raise ScheduleParseError("No course headings recognized; check the HTML or request parameters")

    for crn, section in sections.items():
        section["delivery_mode"] = classify_delivery(
            section["location"], section["notes"],
            any(row["start_time"] and row["end_time"] for row in meetings[crn])
        )
    return sections, meetings, matched_headings


def validate_records(sections: dict[str, dict], meetings: dict[str, list[dict]]) -> None:
    """Fail closed: do not overwrite staging files when schedule data is inconsistent."""
    seen = set()
    for crn, section in sections.items():
        key = (section["term_code"], crn)
        if key in seen or not re.fullmatch(r"\d{3,8}", crn):
            raise ScheduleParseError(f"Invalid/duplicate term and CRN: {key}")
        seen.add(key)
        if not section["course_code"] or not section["section_number"]:
            raise ScheduleParseError(f"CRN {crn}: missing course code or section number")
        if section["campus"] == "**" or section["section_status"] in {"", "**"}:
            raise ScheduleParseError(f"CRN {crn}: invalid placeholder field")
    for crn, records in meetings.items():
        if crn not in sections:
            raise ScheduleParseError(f"Orphaned meeting for CRN {crn}")
        seen_meetings = set()
        for row in records:
            if row["term_code"] != sections[crn]["term_code"]:
                raise ScheduleParseError(f"CRN {crn}: meeting term mismatch")
            if row["day_of_week"] not in DAY_MAP.values():
                raise ScheduleParseError(f"CRN {crn}: invalid day {row['day_of_week']}")
            if bool(row["start_time"]) != bool(row["end_time"]):
                raise ScheduleParseError(f"CRN {crn}: only one meeting time endpoint is present")
            if row["start_time"] and row["start_time"] >= row["end_time"]:
                raise ScheduleParseError(f"CRN {crn}: invalid meeting time")
            signature = (row["day_of_week"], row["start_time"], row["end_time"], row["location"])
            if signature in seen_meetings:
                raise ScheduleParseError(f"CRN {crn}: duplicate meeting {signature}")
            seen_meetings.add(signature)


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    """Write into a temporary file first so an exception cannot truncate a prior CSV."""
    fd, filename = tempfile.mkstemp(dir=str(path.parent), suffix=".csv.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(filename, path)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def main() -> None:
    parser = argparse.ArgumentParser(description="Marshall section and meeting scraper")
    parser.add_argument("--term", default=DEFAULT_TERM, help="MUBERT term code, e.g. 202701")
    parser.add_argument("--subject", default="%", help="Subject code, e.g. CS; default all")
    parser.add_argument("--level", choices=["U", "G", "both"], default="both")
    parser.add_argument("--test", action="store_true", help="Scrape CS in both levels, into *_test.csv")
    args = parser.parse_args()
    if args.test:
        args.subject = "CS"
    if not re.fullmatch(r"\d{6}", args.term):
        parser.error("--term must be a six-digit MUBERT term code")

    subject = args.subject.upper()
    levels = ["U", "G"] if args.level == "both" else [args.level]
    all_sections: dict[str, dict] = {}
    all_meetings: dict[str, list[dict]] = {}
    errors: list[str] = []

    with httpx.Client(
        timeout=90.0,
        follow_redirects=True,
        headers={"User-Agent": "Marshall University Course Advisor Academic Data Project"},
    ) as client:
        for level in levels:
            params = {
                "term": args.term,
                "subject": subject,
                "showschedule": level,
                "campuses": CAMPUSES,
                "termparts": TERM_PARTS,
            }
            print(f"Fetching term {args.term}, level {level}, subject {subject}...")
            try:
                response = client.get(SCHEDULE_URL, params=params)
                response.raise_for_status()
                sections, meetings, course_count = parse_schedule(
                    response.text, str(response.url), args.term
                )
                for crn, section in sections.items():
                    if crn in all_sections:
                        if all_sections[crn]["course_code"] != section["course_code"]:
                            raise ScheduleParseError(
                                f"CRN {crn} conflicts across levels: "
                                f"{all_sections[crn]['course_code']} vs {section['course_code']}"
                            )
                        continue
                    all_sections[crn] = section
                    all_meetings[crn] = meetings[crn]
                print(f"  Found {course_count} course headings and {len(sections)} sections")
            except (httpx.HTTPError, ScheduleParseError) as exc:
                errors.append(f"{level}: {exc}")
                print(f"  ERROR: {errors[-1]}")

    if errors or not all_sections:
        print("Scrape incomplete; existing CSV files were NOT overwritten.")
        raise SystemExit(1)

    try:
        validate_records(all_sections, all_meetings)
    except ScheduleParseError as exc:
        print(f"VALIDATION ERROR: {exc}; existing CSV files were NOT overwritten.")
        raise SystemExit(1) from exc

    suffix = "_test" if args.test or subject != "%" or args.level != "both" else ""
    sections_file = OUTPUT_DIR / f"sections{suffix}.csv"
    meetings_file = OUTPUT_DIR / f"section_meetings{suffix}.csv"
    sections_sorted = sorted(all_sections.values(), key=lambda s: (s["course_code"], int(s["crn"])))
    meeting_rows = [row for crn in sorted(all_meetings, key=int) for row in all_meetings[crn]]
    # Reconcile section course codes against the existing catalog master list.
    catalog_file = OUTPUT_DIR / "courses.csv"
    unmatched_rows = []
    if catalog_file.exists():
        with catalog_file.open(encoding="utf-8-sig", newline="") as f:
            catalog_codes = {r["course_code"].strip().upper()
                             for r in csv.DictReader(f) if r.get("course_code")}
        missing = sorted({s["course_code"] for s in sections_sorted} - catalog_codes)
        if missing:
            unmatched_rows = [{"course_code": code, "term_code": args.term,
                               "crns": ";".join(sorted(s["crn"] for s in sections_sorted if s["course_code"] == code))}
                              for code in missing]
            print(f"WARNING: {len(missing)} scheduled course codes absent from courses.csv")
            print("  Examples:", ", ".join(missing[:20]))
            print("  Resolve these before seeding: coursesection.course_id must reference course.")
        else:
            print("All scheduled course codes matched the catalog CSV.")
    else:
        print("WARNING: No courses.csv found; cannot validate course-code references yet.")

    unmatched_file = OUTPUT_DIR / f"unmatched_courses{suffix}.csv"
    write_csv(unmatched_file, ["course_code", "term_code", "crns"], unmatched_rows)
    print(f"Saved {len(unmatched_rows)} unmatched course codes -> {unmatched_file}")
    write_csv(sections_file, SECTION_FIELDS, sections_sorted)
    write_csv(meetings_file, MEETING_FIELDS, meeting_rows)
    print(f"Saved {len(sections_sorted)} sections -> {sections_file}")
    print(f"Saved {len(meeting_rows)} individual meeting days -> {meetings_file}")
    print("No Supabase tables were modified.")
    if unmatched_rows:
        print("IMPORTANT: Resolve unmatched course codes before importing into Supabase.")


if __name__ == "__main__":
    main()
