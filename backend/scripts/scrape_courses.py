import csv
import re

import httpx
from backend.models import models
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import urljoin


BASE_URL = "https://mubert.marshall.edu"

OUTPUT_FILE = Path(__file__).resolve().parent / "courses.csv"


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

    # Variable-credit format such as "3 to 12" or "3-12"
    range_match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(?:to|-)\s*(\d+(?:\.\d+)?)",
        credit_text,
        re.IGNORECASE
    )

    if range_match:
        min_credits = float(range_match.group(1))
        max_credits = float(range_match.group(2))

        return min_credits, max_credits

    # Normal single-credit format such as "3" or "4.0"
    single_match = re.fullmatch(
        r"\d+(?:\.\d+)?",
        credit_text
    )

    if single_match:
        credits = float(credit_text)

        return credits, credits

    # Unknown format
    return None


url = (
    "https://mubert.marshall.edu/"
    "scheduleofcourses.php?"
    "term=202701"
    "&subject=CS"
    "&showschedule=U"
    "&campuses=|1|2|3|E|H|T|"
    "&termparts=|1|2|3|"
)


courses = []


with httpx.Client() as client:
    response = client.get(url)
    response.raise_for_status()

    soup = BeautifulSoup(response.content, "html.parser")

    for h4 in soup.find_all("h4"):
        link = h4.find("a")

        if not link:
            continue

        # Example:
        # "CS 210 - Data Structures"
        # becomes "CS 210"
        full_course_code = (
            h4.get_text(" ", strip=True)
            .split(" - ")[0]
        )

        try:
            subject, number = full_course_code.split()
        except ValueError:
            print(
                f"Could not parse course code: "
                f"{full_course_code}"
            )
            continue

        table = h4.find_next("table")

        min_credit_hours = None
        max_credit_hours = None

        if table:
            rows = table.find_all("tr")

            if len(rows) > 1:
                cells = rows[1].find_all("td")

                if len(cells) >= 3:
                    credit_text = cells[2].get_text(
                        " ",
                        strip=True
                    )

                    credit_range = parse_credit_hours(
                        credit_text
                    )

                    if credit_range is None:
                        print(
                            f"Could not parse credits for "
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
                f"No credit hours found for "
                f"{full_course_code}"
            )
            continue

        source_url = urljoin(
            BASE_URL,
            link["href"]
        )

        course = models.Course(
            course_code=full_course_code,
            subject_code=subject,
            course_number=number,
            course_title=link.get_text(
                " ",
                strip=True
            ),
            description=get_description(
                client,
                link["href"]
            ),
            min_credit_hours=min_credit_hours,
            max_credit_hours=max_credit_hours,
            active_status=True,
            source_url=source_url
        )

        courses.append(course)


with open(
    OUTPUT_FILE,
    "w",
    newline="",
    encoding="utf-8"
) as file:

    writer = csv.DictWriter(
        file,
        fieldnames=models.Course.model_fields.keys()
    )

    writer.writeheader()

    for course in courses:
        writer.writerow(course.model_dump())


print(f"Scraped {len(courses)} courses")