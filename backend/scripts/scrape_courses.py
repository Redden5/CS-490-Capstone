import csv
import re

import httpx
from backend.models import models
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import urljoin


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

BASE_URL = "https://mubert.marshall.edu"
SCHEDULE_URL = f"{BASE_URL}/scheduleofcourses.php"

TERM = "202701"

CAMPUSES = "|1|2|3|E|H|T|"
TERM_PARTS = "|1|2|3|"

# U = Undergraduate
# G = Graduate
COURSE_LEVELS = ["U", "G"]

OUTPUT_FILE = Path(__file__).resolve().parent / "courses.csv"


# ------------------------------------------------------------
# Get course description
# ------------------------------------------------------------

def get_description(client, href):
    """
    Retrieve the description page for a course and return
    its text content.
    """
    url = urljoin(BASE_URL, href)

    response = client.get(url)
    response.raise_for_status()

    soup = BeautifulSoup(response.content, "html.parser")

    return soup.get_text(" ", strip=True)


# ------------------------------------------------------------
# Parse credit hours
# ------------------------------------------------------------

def parse_credit_hours(credit_text):
    """
    Convert Marshall's credit-hour text into minimum and
    maximum numeric values.

    Examples:
        "3"       -> (3.0, 3.0)
        "3.0"     -> (3.0, 3.0)
        "3 to 12" -> (3.0, 12.0)
        "1-4"     -> (1.0, 4.0)

    Returns None if the value cannot be parsed.
    """

    credit_text = credit_text.strip()

    # Variable-credit courses:
    # "3 to 12"
    # "3-12"
    range_match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(?:to|-)\s*(\d+(?:\.\d+)?)",
        credit_text,
        re.IGNORECASE
    )

    if range_match:
        min_credits = float(range_match.group(1))
        max_credits = float(range_match.group(2))

        return min_credits, max_credits

    # Normal course:
    # "3"
    # "3.0"
    single_match = re.fullmatch(
        r"\d+(?:\.\d+)?",
        credit_text
    )

    if single_match:
        credits = float(credit_text)

        return credits, credits

    return None


# ------------------------------------------------------------
# Scrape one schedule page
# ------------------------------------------------------------

def scrape_course_level(client, level, courses):
    """
    Scrape all courses for either undergraduate (U)
    or graduate (G).

    courses is a dictionary keyed by course_code so that
    duplicate courses are automatically avoided.
    """

    level_name = (
        "Undergraduate"
        if level == "U"
        else "Graduate"
    )

    print(f"Scraping {level_name} courses...")

    params = {
        "term": TERM,
        "subject": "%",
        "showschedule": level,
        "campuses": CAMPUSES,
        "termparts": TERM_PARTS
    }

    response = client.get(
        SCHEDULE_URL,
        params=params
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.content,
        "html.parser"
    )

    for h4 in soup.find_all("h4"):

        link = h4.find("a")

        if not link:
            continue

        heading_text = h4.get_text(
            " ",
            strip=True
        )

        # Expected format:
        #
        # CS 210 - Data Structures
        #
        # Extract:
        # subject = CS
        # number = 210

        course_match = re.match(
            r"^([A-Z][A-Z0-9]*)\s+"
            r"([A-Z0-9-]+)\s+-",
            heading_text
        )

        if not course_match:
            continue

        subject = course_match.group(1)
        number = course_match.group(2)

        full_course_code = (
            f"{subject} {number}"
        )

        # If we already found this course,
        # don't scrape it again.
        if full_course_code in courses:
            continue

        # ----------------------------------------------------
        # Find credit hours
        # ----------------------------------------------------

        table = h4.find_next("table")

        min_credit_hours = None
        max_credit_hours = None

        if table:
            rows = table.find_all("tr")

            if len(rows) > 1:
                cells = rows[1].find_all("td")

                if len(cells) >= 3:

                    credit_text = (
                        cells[2]
                        .get_text(
                            " ",
                            strip=True
                        )
                    )

                    credit_range = (
                        parse_credit_hours(
                            credit_text
                        )
                    )

                    if credit_range is None:
                        print(
                            f"Could not parse "
                            f"credits for "
                            f"{full_course_code}: "
                            f"{credit_text}"
                        )

                        continue

                    (
                        min_credit_hours,
                        max_credit_hours
                    ) = credit_range

        if (
            min_credit_hours is None
            or max_credit_hours is None
        ):
            print(
                f"No credit hours found "
                f"for {full_course_code}"
            )

            continue

        # ----------------------------------------------------
        # Course URL
        # ----------------------------------------------------

        source_url = urljoin(
            BASE_URL,
            link["href"]
        )

        # ----------------------------------------------------
        # Description
        # ----------------------------------------------------

        try:
            description = get_description(
                client,
                link["href"]
            )

        except httpx.HTTPError as error:
            print(
                f"Could not retrieve "
                f"description for "
                f"{full_course_code}: "
                f"{error}"
            )

            description = None

        # ----------------------------------------------------
        # Build Course object
        # ----------------------------------------------------

        course = models.Course(
            course_code=full_course_code,
            subject_code=subject,
            course_number=number,
            course_title=link.get_text(
                " ",
                strip=True
            ),
            description=description,
            min_credit_hours=min_credit_hours,
            max_credit_hours=max_credit_hours,
            active_status=True,
            source_url=source_url
        )

        courses[full_course_code] = course


# ------------------------------------------------------------
# Main scraping process
# ------------------------------------------------------------

courses = {}


with httpx.Client(
    timeout=30.0,
    follow_redirects=True,
    headers={
        "User-Agent":
            "Marshall Course Advisor Academic Data Scraper"
    }
) as client:

    for level in COURSE_LEVELS:

        try:
            scrape_course_level(
                client,
                level,
                courses
            )

        except httpx.HTTPError as error:
            print(
                f"Failed to scrape "
                f"course level {level}: "
                f"{error}"
            )


# ------------------------------------------------------------
# Write CSV
# ------------------------------------------------------------

with open(
    OUTPUT_FILE,
    "w",
    newline="",
    encoding="utf-8"
) as file:

    writer = csv.DictWriter(
        file,
        fieldnames=(
            models.Course
            .model_fields
            .keys()
        )
    )

    writer.writeheader()

    for course_code in sorted(courses):

        course = courses[course_code]

        writer.writerow(
            course.model_dump()
        )


# ------------------------------------------------------------
# Results
# ------------------------------------------------------------

print()
print(
    f"Scraped {len(courses)} "
    f"unique courses"
)

print(
    f"Saved to: {OUTPUT_FILE}"
)