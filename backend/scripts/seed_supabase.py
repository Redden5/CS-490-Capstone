import csv
import os

from pathlib import Path
from supabase import create_client
from backend.models.models import Course
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")



supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SECRET_KEY"]
)

courses = []

with open("courses.csv", newline="") as file:
    reader = csv.DictReader(file)

    for row in reader:
        course = Course(**row)
        courses.append(course.model_dump())


supabase.table("Course").upsert(
    courses,
    on_conflict="course_code"
).execute()

print(f"Seeded {len(courses)} courses")