"""Run offline: python -m unittest discover -s backend/tests -p 'test_*.py'"""
import json
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from backend.services import schedule_service as s


def section(section_id, code, *, meetings=None, status="OPEN", cap=30, enrolled=10):
    return {"section_id": section_id, "course_id": section_id // 100,
            "section_status": status, "capacity": cap, "enrolled": enrolled,
            "meetings": meetings if meetings is not None else []}


def meeting(day, start, end):
    return {"day_of_week": day, "start_time": start, "end_time": end}


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.a1 = section(101, "CS 120", meetings=[meeting(2, "09:00:00", "10:15:00"),
                                                      meeting(4, "09:00:00", "10:15:00")])
        self.a2 = section(102, "CS 120", meetings=[meeting(2, "14:00:00", "15:15:00")])
        self.b1 = section(201, "CS 205", meetings=[meeting(2, "10:15:00", "11:30:00"),
                                                      meeting(4, "09:30:00", "10:45:00")])
        self.b2 = section(202, "CS 205", meetings=[meeting(2, "12:30:00", "13:45:00")])
        self.grouped = {"CS 120": [self.a1, self.a2], "CS 205": [self.b1, self.b2]}
        self.credits = {"CS 120": Decimal("3"), "CS 205": Decimal("4")}

    def test_meetings_conflict_in_second_pair_and_boundary_not_overlap(self):
        self.assertTrue(s.sections_conflict(self.a1, self.b1))
        self.assertFalse(s.meeting_conflict(meeting(2, "09:00", "10:15"),
                                            meeting(2, "10:15", "11:15")))
        self.assertFalse(s.sections_conflict(self.a1, self.b2))

    def test_unknown_meetings_create_tentative_plan(self):
        unknown = section(999, "CS 205", status="UNKNOWN")
        result = s.generate_schedule_options({"CS 120": [self.a1], "CS 205": [unknown]},
                                             self.credits, {"CS 120", "CS 205"},
                                             eligibility_statuses={"CS 120": "MET", "CS 205": "MET"},
                                             target_credits=7)
        self.assertTrue(result["options"])
        combined = result["options"][0]
        self.assertEqual(combined["status"], "TENTATIVE")
        self.assertIn("CS 205:MEETING_TIMES_INCOMPLETE_OR_TBA", combined["warnings"])

    def test_generator_selects_nonconflicting_sections(self):
        options = s.generate_schedule_options(self.grouped, self.credits, {"CS 120", "CS 205"},
                                             eligibility_statuses={"CS 120": "MET", "CS 205": "MET"},
                                             target_credits=7)
        self.assertTrue(options["options"])
        self.assertEqual(options["options"][0]["total_credits"], 7)
        for opt in options["options"]:
            vals = list(opt["sections"].values())
            if len(vals) == 2:
                self.assertFalse(s.sections_conflict(vals[0], vals[1]))
        # Exact 21h admin policy, no bypass without approval.
        with self.assertRaises(ValueError):
            s.generate_schedule_options(self.grouped, self.credits, set(self.grouped), max_credits=22)

    def test_unmet_and_full_sections_are_excluded(self):
        full = section(555, "CS 120", meetings=[meeting(2, "08:00", "09:00")], cap=15, enrolled=15)
        x = s.generate_schedule_options({"CS 120": [full], "CS 205": [self.b2]},
                                        self.credits, {"CS 120", "CS 205"},
                                        eligibility_statuses={"CS 120": "MET", "CS 205": "UNMET"})
        self.assertFalse(x["options"])
        self.assertEqual(x["excluded_courses"]["CS 205"], "PREREQUISITES_UNMET")

    def test_unscheduled_mandatory_not_erased(self):
        x = s.generate_schedule_options({"CS 120": [self.a2], "CS 205": []}, self.credits,
                                        {"CS 120", "CS 205"}, target_credits=7)
        self.assertTrue(x["options"])
        self.assertIn("CS 205", x["options"][0]["unscheduled_required"])

    def test_swap_section_validates_conflicts(self):
        active = {"CS 120": self.a2, "CS 205": self.b1}
        ok = s.replace_section(active, "CS 120", 101, self.grouped, self.credits,
                               eligibility_statuses={"CS 120": "MET", "CS 205": "MET"})
        self.assertFalse(ok["ok"])  # collision on day 4
        self.assertEqual(active["CS 120"]["section_id"], 102)  # original unchanged
        ok = s.replace_section(active, "CS 205", 202, self.grouped, self.credits,
                               eligibility_statuses={"CS 120": "MET", "CS 205": "MET"})
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["sections"]["CS 205"]["section_id"], 202)

    def test_flexible_swap_preserves_mandatory(self):
        choices = [{"requirement_id": "elective1", "course_codes": ["CS 205", "CS 310"],
                    "max_courses_this_term": 1}]
        alt = section(310, "CS 310", meetings=[meeting(1, "09:00", "10:00")])
        grouped = {**self.grouped, "CS 310": [alt]}
        credits = {**self.credits, "CS 310": Decimal("3")}
        base = {"CS 120": self.a1, "CS 205": self.b2}
        not_allowed = s.replace_flexible_course(base, "CS 120", "CS 310", 310,
                                                grouped, credits, {"CS 120"}, choices)
        self.assertFalse(not_allowed["ok"])
        accepted = s.replace_flexible_course(base, "CS 205", "CS 310", 310,
                                            grouped, credits, {"CS 120"}, choices,
                                            eligibility_statuses={"CS 120": "MET", "CS 310": "MET"})
        self.assertTrue(accepted["ok"])
        self.assertIn("CS 120", accepted["sections"])
        self.assertNotIn("CS 205", accepted["sections"])

    def test_credit_range_requires_selection(self):
        courses = [{"course_code": "CS 120", "min_credit_hours": 1, "max_credit_hours": 4,
                    "active_status": True}]
        values, problems = s.course_credit_values(courses)
        self.assertNotIn("CS 120", values)
        self.assertEqual(problems["CS 120"], "VARIABLE_CREDITS_CHOICE_REQUIRED")
        values, problems = s.course_credit_values(courses, {"CS 120": 3})
        self.assertEqual(values["CS 120"], Decimal("3"))

    def test_grouping_attaches_meetings_without_duplicate_catalog_read(self):
        course_rows = [{"course_id": 10, "course_code": "CS 120"}]
        section_rows = [{"section_id": 101, "course_id": 10, "term_id": 1}]
        meeting_rows = [{"meeting_id": 3, "section_id": 101, "day_of_week": 2,
                         "start_time": "12:30:00", "end_time": "13:45:00"}]
        with patch.object(s, "get_course_ids", return_value=course_rows) as mocked_course, \
             patch.object(s, "_sections_for_ids", return_value=section_rows), \
             patch.object(s, "get_section_meetings", return_value=meeting_rows):
            result = s.group_sections_by_course({"CS 120", "CS 205"}, 1)
        self.assertEqual(mocked_course.call_count, 1)
        self.assertEqual(result["CS 120"][0]["meetings"], meeting_rows)
        self.assertEqual(result["CS 205"], [])

    def test_contract_fixture_keeps_review_required_science_out_of_autofill(self):
        fixture = Path(__file__).parent / "fixtures" / "planning_handoff_v1_example.json"
        if not fixture.exists():
            self.skipTest("Optional fixture not supplied")
        handoff = json.loads(fixture.read_text(encoding="utf-8"))
        plan = s.read_explicit_plan(handoff)
        self.assertEqual(len(plan["required_courses"]), 24)
        self.assertEqual(plan["eligibility_statuses"], {})
        self.assertTrue(any("CHOICE_GROUP_REQUIRES_REVIEW" in w for w in plan["warnings"]))


if __name__ == "__main__":
    unittest.main()

# The suite's offline tests intentionally do not connect to Supabase. The production
# integration can be validated with a staging project after credentials are configured.

class DegreePriorityTests(unittest.TestCase):
    def test_required_credits_take_priority_over_unrelated_optional_fill(self):
        def sect(sid):
            return {"section_id": sid, "section_status": "OPEN", "capacity": 20,
                    "enrolled": 5, "meetings": [{"day_of_week": sid,
                    "start_time": "09:00:00", "end_time": "10:00:00"}]}
        sections = {"CS 120": [sect(1)], "CS 205": [sect(2)], "ENG 201": [sect(3)]}
        credits = {"CS 120": 3, "CS 205": 3, "ENG 201": 3}
        result = s.generate_schedule_options(sections, credits, {"CS 120", "CS 205"},
                     choice_groups=[{"requirement_id": "elective", "max_courses_this_term": 1,
                                     "course_codes": ["ENG 201"]}],
                     eligibility_statuses={c: "MET" for c in credits}, target_credits=6)
        self.assertEqual(set(result["options"][0]["sections"]), {"CS 120", "CS 205"})

    def test_high_level_handoff_with_fake_database(self):
        handoff = {"schema_version": "1.0.0", "requirements": [
            {"kind": "REQUIRED_COURSE", "planning_disposition": "CANDIDATE_COURSEWORK_REMAINING",
             "remaining_required_course_codes": ["CS 120", "CS 205"]}],
             "course_eligibility": {"evaluation_status": "NOT_EVALUATED", "courses": []}}
        courses = [{"course_id": 1, "course_code": "CS 120", "min_credit_hours": 3,
                    "max_credit_hours": 3, "active_status": True},
                   {"course_id": 2, "course_code": "CS 205", "min_credit_hours": 3,
                    "max_credit_hours": 3, "active_status": True}]
        sections = [{"section_id": 10, "course_id": 1, "term_id": 1,
                     "crn": "1234", "section_status": "OPEN", "capacity": 25, "enrolled": 2}]
        meetings = [{"meeting_id": 100, "section_id": 10, "day_of_week": 2,
                     "start_time": "12:30:00", "end_time": "13:45:00"}]
        with patch.object(s, "get_course_ids", return_value=courses), \
             patch.object(s, "_sections_for_ids", return_value=sections), \
             patch.object(s, "get_section_meetings", return_value=meetings):
            output = s.generate_from_handoff(handoff, term_id=1, target_credits=6)
        self.assertEqual(output["options"][0]["total_credits"], 3.0)
        self.assertEqual(output["options"][0]["status"], "TENTATIVE")
        self.assertEqual(output["options"][0]["unscheduled_required"], ["CS 205"])
