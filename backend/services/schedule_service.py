import json

from backend.db.supabase_client import supabase


def get_missing_required_course(handoff: dict) -> set[str]:
    missing_courses = set()

    for requirement in handoff["requirements"]:
        if requirement["kind"] == "REQUIRED_COURSE" and requirement[
            "planning_disposition"] == "CANDIDATE_COURSEWORK_REMAINING":
            missing_courses.update(requirement["remaining_required_course_codes"])

    return missing_courses


def get_available_sections(course_codes: set[str], term_id: int):
    courses = get_course_ids(course_codes)
    course_ids = [course["course_id"] for course in courses]
    if not course_ids:
        return []
    response = supabase.table("coursesection").select("section_id,course_id,term_id,crn,section_number").in_("course_id", course_ids).eq("term_id",
        term_id).execute()

    return response.data

def get_course_ids(course_codes: set[str]):
    if not course_codes:
        return []

    response = supabase.table("course").select("course_id,course_code").in_("course_code",list(course_codes)).execute()


    return response.data

with open("planning_handoff_v1_example.json") as file:
    handoff = json.load(file)
missing = get_missing_required_course(handoff)
print("Total missing courses:", len(missing))
print("Missing courses:", sorted(missing))
course_codes = {"CS 120", "CS 205"}

sections = get_available_sections(course_codes, 1)

for section in sections:
    print(section)