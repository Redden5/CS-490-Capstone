import csv
import os

from pathlib import Path
from supabase import create_client
from backend.models.models import Course
from dotenv import load_dotenv


# Project root
BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

# Assumes courses.csv is in the same folder as this script
CSV_FILE = Path(__file__).resolve().parent / "courses.csv"


# Connect to Supabase
supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SECRET_KEY"]
)


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