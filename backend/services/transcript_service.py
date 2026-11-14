import re

from pydantic import TypeAdapter

from backend.models.models import TranscriptCourse
from backend.utils.pdf_extractor import extract_pdf_text
from backend.models import models
#Python caches recently used regex patterns could be optimized
#Term can return None for courses that are transfer or do not
TRANSFER_COURSE_PATTERN = re.compile(r"(?P<subject_code>[A-Z]{2,4})\s+"
                            r"(?P<course_number>[0-9A-Z]{3,4})\s+"
                            r"(?P<course_title>.+?)\s+"
                            r"(?P<grade>[A-Z]{1,2})\.\s+"
                            r"(?P<credits>\d+\.\d+)\s+"
                            r"(?P<quality_points>\d+\.\d+)$"
                            )

INSTITUTION_COURSE_PATTERN =  re.compile(r"(?P<subject_code>[A-Z]{2,4})\s+"
                            r"(?P<course_number>[0-9A-Z]{3,4})\s+"
                            r"(?P<level>[0-9]{1,2})\s+"
                            r"(?P<course_title>.+?)\s+"
                            r"(?P<grade>[A-Z]{1,2})\s+"
                            r"(?P<credits>\d+\.\d+)\s+"
                            r"(?P<quality_points>\d+\.\d+)$")

COURSE_IN_PROGRESS_PATTERN = re.compile(r"(?P<subject_code>[A-Z]{2,4})\s+"
                            r"(?P<course_number>[0-9A-Z]{3,4})\s+"
                            r"(?P<level>[0-9]{1,2})\s+"
                            r"(?P<course_title>.+?)\s+"
                            r"(?P<credits>\d+\.\d{3})$"
)
INSTITUTION_PREFIX_PATTERN = re.compile(r"^(?P<subject_code>[A-Z]{2,4})\s+"
                  r"(?P<course_number>[0-9A-Z]{3,4})\s+"
                  r"(?P<level>\d{1,2})\s+")
INSTITUTION_GRADE_PATTERN = re.compile(r"(?P<grade>[A-Z])\s+"
                     r"(?P<credits>\d+\.\d{3})\s+"
                     r"(?P<quality_points>\d+\.\d{2})$")

def parse_transcript(text: str) -> list[dict]:
    """Parses full transcript information decides what state it is on based on current section and current term then
    calls on parse_course_line to decide if course fits regex """

    courses = []
    current_section = None
    current_term = None
    current_institution = None

    lines = text.splitlines()
    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
    # for line in text.splitlines():
    #     line = line.strip()

        match line:
            case "TRANSFER CREDIT ACCEPTED BY INSTITUTION":
                current_section = "transfer"
                continue
            case "INSTITUTION CREDIT":
                current_section = "institution"
                current_term = None
                current_institution = None
                continue
            case "COURSE(S) IN PROGRESS":
                current_term = None
                current_institution = None
                current_section = "in_progress"
                continue
            case "TRANSCRIPT TOTALS":
                current_term = None
                current_institution = None
                current_section = None
                continue

        if current_section == "transfer":
            heading = parse_transfer_heading(line)

            if heading is not None:
                current_term, current_institution = heading

        if line.startswith("Term: "):
            current_term = line.split(":", 1)[1].strip()

        course = parse_course_line(line, current_section)

        if course is not None:
            course["term"] = current_term
            course["institution"] = current_institution
            courses.append(course)

            # Recovers indented title lines
            if i + 1 < len(lines):
                next_line = lines[i+1]
                if is_title_continuation(next_line,current_section):
                    course["course_title"] += " " + next_line.strip()

        # Possible course check
        # elif current_section is not None:
        #     possible_course = re.match(r"^[A-Z]{2,4}\s+[0-9A-Z]{3,4}\s+",line)
        #     if possible_course:
        #         print("Possible Course: " ,line)

    return courses

def parse_course_line(line:str, current_section:str | None)-> dict | None:
    """Parses single course lines and decides the pattern selection based on provided state then decides
    if course fits full regex and check for missed courses using recover_institution_courses"""
    match current_section:
        case "transfer":
            pattern = TRANSFER_COURSE_PATTERN
        case "institution":
            pattern = INSTITUTION_COURSE_PATTERN
        case "in_progress":
            pattern = COURSE_IN_PROGRESS_PATTERN
        case _:
            return None

    match = pattern.fullmatch(line.strip())

    if match is not None:
        return match.groupdict()

    if current_section == "institution":
        return recover_institution_courses(line)

    return None


def recover_institution_courses(line: str)-> dict | None:
    """Recovers institution courses based on prefix regex and grade letter regex.
    This was created because extracting issues caused courses to have some attributes join together with each other."""
    grade_match = re.search(INSTITUTION_GRADE_PATTERN, line)
    prefix_match = re.match(INSTITUTION_PREFIX_PATTERN, line)

    if grade_match is None or prefix_match is None:
        return None

    course = {}
    title = line[prefix_match.end():grade_match.start("grade")].strip()
    course.update(prefix_match.groupdict())
    course.update({"course_title": title})
    course.update(grade_match.groupdict())

    return course

def is_title_continuation(raw_line:str,current_section:str | None)-> bool:
    if (current_section == "institution"
            and raw_line.startswith(" ")
            and raw_line.strip()
            and not raw_line.strip().startswith("Term Totals")
    ):
        return True
    return False

def parse_transfer_heading(line:str)-> tuple[str,str] | None:
    """Parses transfer term headings"""
    parts = line.split(":", 1)

    if len(parts) != 2:
        return None

    raw_term = parts[0].strip()
    institution = parts[1].strip()

    match = re.fullmatch(r"[A-Z]{2}\d{2}", raw_term)

    if match and institution:
        term = normalize_transfer_term(raw_term)
        return term, institution

    return None

def normalize_transfer_term(raw_term:str)-> str:
    semester_code = raw_term[:2]
    year_code = raw_term[2:]
    semesters = {
        "FA": "Fall",
        "SP": "Spring",
        "SU": "Summer"
    }

    semester = semesters.get(semester_code)
    if semester is None:
        return raw_term
    year = 2000 + int(year_code)
    return f"{semester} {year}"


