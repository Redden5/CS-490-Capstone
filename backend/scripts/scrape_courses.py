import csv

import httpx
from backend.models import models
from bs4 import BeautifulSoup
from urllib.parse import urljoin


BASE_URL = "https://mubert.marshall.edu"

def get_description(client, href):
    url = urljoin(BASE_URL, href)

    response = client.get(url)
    response.raise_for_status()

    soup = BeautifulSoup(response.content, "html.parser")
    return soup.get_text(" ", strip=True)

url = ("https://mubert.marshall.edu/scheduleofcourses.php?term=202701&subject=CS&showschedule=U&campuses=|1|2|3|E|H|T|&termparts=|1|2|3|")

courses = []

with httpx.Client() as client:
    response = client.get(url)
    response.raise_for_status()

    soup = BeautifulSoup(response.content, "html.parser")

    for h4 in soup.find_all("h4"):
        link = h4.find("a")

        if not link:
            continue

        full_course_code = h4.get_text(" ", strip=True).split(" - ")[0]
        subject, number = full_course_code.split()

        table = h4.find_next("table")

        credit_hours = None

        if table:
            first_row = table.find("tr")
            rows = table.find_all("tr")

            if len(rows) > 1:
                cells = rows[1].find_all("td")

                if len(cells) >= 3:
                    credit_hours = cells[2].get_text(" ", strip=True)

        course = models.Course(
            course_code=full_course_code,
            subject_code=subject,
            course_number=number,
            course_title=link.get_text(" ", strip=True),
            description=get_description(client, link["href"]),
            credit_hours=credit_hours,
            active_status=True
        )

        courses.append(course)

    with open("courses.csv", "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames = models.Course.model_fields.keys()
        )
        writer.writeheader()

        for course in courses:
            writer.writerow(course.model_dump())



#Writes a CSV of all courses