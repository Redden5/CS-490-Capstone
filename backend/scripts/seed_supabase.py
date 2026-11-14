import csv
from pathlib import Path

from backend.db.supabase_client import supabase
from backend.models.models import Course


# Assumes courses.csv is in the same folder as this script
CSV_FILE = Path(__file__).resolve().parent / "courses.csv"


courses = []

# Read courses from CSV
with open(
    CSV_FILE,
    newline="",
    encoding="utf-8"
) as file:

    reader = csv.DictReader(file)

    for row in reader:
        course = Course(**row)
        courses.append(course.model_dump())


# Insert/update courses in Supabase
if courses:
    supabase.table("course").upsert(
        courses,
        on_conflict="course_code"
    ).execute()


print(f"Seeded {len(courses)} courses")