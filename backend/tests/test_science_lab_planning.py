"""CS science progress through the shared planner, using local catalog evidence."""

import csv
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app
from backend.routes.planning import get_client_factory
from backend.scripts.parse_prerequisites import parse_rows
from backend.services.degree_planning import run_degree_plan
from backend.tests.test_core_ii_planning import Client, DRAFT, ROOT, YEAR
from backend.tests.test_prerequisite_loader import tables_from_source


PAIRS = [
    ("BSC 120", "BSC 120L"), ("CHM 211", "CHM 217"), ("GLY 200", "GLY 210L"),
    ("PHY 201", "PHY 202"), ("PHY 211", "PHY 202"),
]
MAJOR_CODES = {code for pair in PAIRS for code in pair}
NATURAL = "Core II Natural Sciences"


def catalog_rows(filename):
    with (ROOT / "backend/scripts" / filename).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


class ScienceLabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.draft = json.loads(DRAFT.read_text(encoding="utf-8"))
        codes = MAJOR_CODES | {"PS 111", "BSC 121", "BSC 121L", "MTH 229", "MTH 229H"}
        rules = [row for row in catalog_rows("catalog_prerequisites.csv")
                 if row["course_code"] == "PHY 211" and row["catalog_year"] == YEAR]
        cls.tables = tables_from_source(codes, parse_rows(rules, codes))
        catalog = {row["course_code"]: row for row in catalog_rows("courses.csv")}
        ids = {}
        for course in cls.tables["course"]:
            code = course["course_code"]
            ids[code] = course["course_id"]
            for key in ("min_credit_hours", "max_credit_hours"):
                course[key] = float(catalog[code][key])
            course["source_url"] = catalog[code]["source_url"]
        cls.tables["attribute"] = [{"attribute_id": 1, "attribute_name": NATURAL}]
        cls.tables["courseattribute"] = [
            {"course_id": ids[row["course_code"]], "catalog_year_id": 7, "attribute_id": 1,
             "source_url": row["source_url"], "observed_at": row["observed_at_utc"]}
            for row in catalog_rows("catalog_attributes.csv")
            if row["course_code"] in ids and row["attribute_name"] == NATURAL and row["catalog_year"] == YEAR
        ]

    def setUp(self):
        self.client = Client(self.tables)

    def plan(self, courses, *, draft=None, attributes=True, **kwargs):
        result = run_degree_plan(draft or self.draft, list(courses), client=self.client,
                                 supabase_attributes=attributes, **kwargs)
        requirements = {r["rule_id"]: r for r in result.handoff["requirements"]}
        return requirements["t0_row_0058"], requirements["t0_row_0011"], result.handoff

    def candidates(self, handoff):
        return MAJOR_CODES.intersection(c["course_code"] for c in handoff["course_eligibility"]["courses"])

    def test_each_listed_pair_supports_core_and_one_major_choice(self):
        for pair in PAIRS:
            with self.subTest(pair=pair):
                major, core, handoff = self.plan(pair)
                self.assertEqual(major["choice_progress"]["choices_remaining_candidate"], 2)
                self.assertEqual(core["candidate_progress_status"], "CANDIDATE_MET")
                self.assertEqual(core["attribute_filter"]["candidate_course_codes_entered"], sorted(pair))
                self.assertEqual(core["credit_progress"]["additional_credits_indicated_candidate"], 0)
                self.assertEqual(handoff["distinct_course_credit_evidence"]["known_credit_subtotal"],
                                 core["credit_progress"]["candidate_known_credit_subtotal"])
                self.assertTrue(self.candidates(handoff).isdisjoint(pair))

    def test_three_pairs_stop_science_candidates_even_without_credit_lookup(self):
        courses = [code for pair in PAIRS[:3] for code in pair]
        for attributes in (False, True):
            with self.subTest(attributes=attributes):
                major, core, handoff = self.plan(courses, attributes=attributes)
                self.assertEqual(major["choice_progress"]["choices_remaining_candidate"], 0)
                self.assertFalse(self.candidates(handoff))
                self.assertEqual(len(major["course_options"]), 5)
                if attributes:
                    self.assertEqual(major["planning_disposition"], "CANDIDATE_COMPLETE")
                    self.assertEqual(core["credit_progress"]["candidate_known_credit_subtotal"], 4)
                    self.assertEqual(handoff["distinct_course_credit_evidence"]["known_credit_subtotal"], 13)
                else:
                    self.assertEqual(major["planning_disposition"], "REVIEW_REQUIRED")
                    self.assertEqual(core["planning_disposition"], "REVIEW_REQUIRED")

    def test_partial_and_mismatched_pairs_never_pool_into_one_lab(self):
        courses = ["BSC 120", "CHM 217", "PHY 211"]
        major, core, handoff = self.plan(courses)
        self.assertEqual(major["choice_progress"]["choices_remaining_candidate"], 3)
        self.assertIn("lab_eligibility_unverified", core["remaining_policy_review_flags"])
        self.assertEqual(core["planning_disposition"], "REVIEW_REQUIRED")
        self.assertIsNone(core["credit_progress"]["candidate_known_credit_subtotal"])
        self.assertEqual(self.candidates(handoff), MAJOR_CODES - set(courses) - {"PHY 201"})

    def test_shared_physics_lab_and_duplicate_attempts(self):
        for lectures in (["PHY 201"], ["PHY 211"], ["PHY 211", "PHY 201"]):
            with self.subTest(lectures=lectures):
                major, _, handoff = self.plan([*lectures, "PHY 202", "phy202", lectures[0]])
                self.assertEqual(major["choice_progress"]["fully_entered_options"], len(lectures))
                self.assertEqual(major["choice_progress"]["maximum_nonoverlapping_choices_entered"], 1)
                self.assertEqual(self.candidates(handoff), MAJOR_CODES - {"PHY 201", "PHY 202", "PHY 211"})
        _, _, lab_only = self.plan(["PHY 202"])
        self.assertTrue({"PHY 201", "PHY 211"} <= self.candidates(lab_only))

    def test_ps_111_satisfies_only_core(self):
        major, core, handoff = self.plan(["PS 111"], core_ii_candidates=True)
        self.assertEqual(handoff["schema_version"], "2.1.0")
        self.assertEqual(core["planning_disposition"], "CANDIDATE_COMPLETE")
        self.assertEqual(major["choice_progress"]["choices_remaining_candidate"], 3)
        self.assertEqual(self.candidates(handoff), MAJOR_CODES)

    def test_or_above_substitution_remains_for_review(self):
        major, core, handoff = self.plan(["BSC 121", "BSC 121L"])
        self.assertEqual(major["choice_progress"]["choices_remaining_candidate"], 3)
        self.assertIn("or_above_equivalents_not_enumerated", major["course_options"][0]["review_flags"])
        self.assertEqual(core["planning_disposition"], "REVIEW_REQUIRED")
        self.assertEqual(self.candidates(handoff), MAJOR_CODES)

    def test_unclassified_lab_and_missing_credit_do_not_clear_core(self):
        lab = next(r for r in self.client.tables["course"] if r["course_code"] == "BSC 120L")
        lab["min_credit_hours"] = None
        self.assertEqual(self.plan(PAIRS[0])[1]["planning_disposition"], "REVIEW_REQUIRED")
        lab["min_credit_hours"] = 1
        association = next(r for r in self.client.tables["courseattribute"] if r["course_id"] == lab["course_id"])
        association["catalog_year_id"] = 99
        self.assertEqual(self.plan(PAIRS[0])[1]["planning_disposition"], "REVIEW_REQUIRED")

    def test_other_program_does_not_inherit_science_policy(self):
        _, core, _ = self.plan(PAIRS[0], draft={**self.draft, "program_title": "Other"})
        self.assertEqual(core["planning_disposition"], "REVIEW_REQUIRED")

    def test_http_handoff_preserves_bundles_and_one_physics_choice(self):
        courses = ["PHY 211", "PHY 202"]
        with patch.dict(app.dependency_overrides, {get_client_factory: lambda: lambda: self.client}), TestClient(app) as http:
            response = http.post("/api/v1/planning/evaluate", json={
                "program_key": "computer-science-bs", "catalog_year": YEAR, "completed_courses": courses,
            })
        self.assertEqual(response.status_code, 200, response.text)
        major, core, direct = self.plan(courses, core_ii_candidates=True)
        self.assertEqual(response.json(), direct)
        self.assertEqual(len(major["course_options"]), 5)
        self.assertEqual(core["planning_disposition"], "CANDIDATE_COMPLETE")
        self.assertNotIn("PHY 201", self.candidates(direct))

    def test_missing_physics_prerequisite_keeps_candidate_and_diagnostics(self):
        major, _, handoff = self.plan(["PHY 202"], evaluate_prerequisites=True)
        self.assertEqual(handoff["schema_version"], "2.0.0")
        self.assertEqual(major["course_options"][-1]["missing_codes_to_complete_option"], ["PHY 211"])
        course = next(c for c in handoff["course_eligibility"]["courses"] if c["course_code"] == "PHY 211")
        self.assertTrue(course["loading"]["data_complete"])
        self.assertEqual(course["status"], "UNKNOWN")
        self.assertIn("MTH 229", course["missing_courses"])
        self.assertTrue(course["loading"]["rule_provenance"])
