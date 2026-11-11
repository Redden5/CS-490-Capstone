"""Direct planning calls using the same resolved draft and SELECT-only CLI fixture."""

from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.services import degree_planning as planning
from backend.services import flexible_candidates, planning_eligibility, prerequisite_loader
from backend.tests.test_core_ii_planning import Client, DRAFT, fixture


class DegreePlanningTests(unittest.TestCase):
    def setUp(self):
        self.draft = json.loads(DRAFT.read_text(encoding="utf-8"))
        tables, attributes, catalog, prerequisites = fixture()
        self.client = Client(tables)
        sources = {"catalog_attributes.csv": attributes, "courses.csv": catalog,
                   "catalog_prerequisites.csv": prerequisites}
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.read = stack.enter_context(patch.object(
            Path, "read_bytes", autospec=True, side_effect=lambda path: sources[path.name],
        ))
        stack.enter_context(patch.object(flexible_candidates, "_SOURCE_SHA256", hashlib.sha256(attributes).hexdigest()))
        stack.enter_context(patch.multiple(
            prerequisite_loader, _CATALOG_SHA256=hashlib.sha256(catalog).hexdigest(),
            _PREREQUISITE_SHA256=hashlib.sha256(prerequisites).hexdigest(),
        ))
        for target in ("_create_attribute_client", "_create_prerequisite_client"):
            stack.enter_context(patch.object(planning, target, side_effect=AssertionError("Unexpected client creation")))
        for target in ("argparse.ArgumentParser.parse_args", "builtins.print", "pathlib.Path.write_text"):
            stack.enter_context(patch(target, side_effect=AssertionError("Unexpected CLI side effect")))

    def test_v1_default_and_preview_only_are_offline(self):
        result = planning.run_degree_plan(self.draft, ["cs1", "CS 1"])
        self.assertEqual(result.handoff["schema_version"], "1.0.0")
        self.assertEqual(result.preview["completed_course_codes_entered"], ["CS 1"])
        preview_only = planning.run_degree_plan(self.draft, ["CS 1"], include_handoff=False)
        self.assertEqual(preview_only.preview, result.preview)
        self.assertIsNone(preview_only.handoff)
        self.read.assert_not_called()

    def test_v2_grade_and_repeat_evidence_affects_only_eligibility(self):
        graded = {"course_code": "cs110", "grade": " a "}
        for records, status in (([graded], "MET"), (["CS 110"], "UNKNOWN"), ([graded, "CS110"], "UNKNOWN")):
            with self.subTest(records=records):
                before = deepcopy(records)
                result = planning.run_degree_plan(self.draft, records, evaluate_prerequisites=True, client=self.client)
                courses = {c["course_code"]: c for c in result.handoff["course_eligibility"]["courses"]}
                self.assertEqual(result.handoff["schema_version"], "2.0.0")
                self.assertEqual(result.preview["completed_course_codes_entered"], ["CS 110"])
                self.assertNotIn("CS 110", courses)
                self.assertEqual(courses["CS 210"]["status"], status)
                self.assertEqual(records, before)

    def test_v21_joins_all_memberships_to_one_prerequisite_batch(self):
        with patch.object(planning_eligibility, "load_prerequisite_rules",
                          wraps=prerequisite_loader.load_prerequisite_rules) as load:
            result = planning.run_degree_plan(
                self.draft, ["CS 110", "art210"], reported_credits={"ART210": 1},
                core_ii_candidates=True, client=self.client,
            )
        handoff = result.handoff
        self.assertEqual(handoff["schema_version"], "2.1.0")
        self.assertEqual(handoff["distinct_course_credit_evidence"]["known_credit_subtotal"], 4)
        block = handoff["flexible_candidates"]
        self.assertEqual(block["course_requirement_memberships"]["HST 101"], ["t0_row_0012", "t0_row_0013"])
        courses = [c["course_code"] for c in handoff["course_eligibility"]["courses"]]
        self.assertEqual(courses, sorted(set(courses)))
        self.assertTrue({"CS 110", "ART 210"}.isdisjoint(courses))
        load.assert_called_once()
        self.assertIs(load.call_args.args[0], self.client)
        self.assertEqual(load.call_args.args[1], courses)
        for requirement in block["requirements"]:
            for candidate in requirement["candidates"]:
                self.assertIn(candidate["course_eligibility_reference"], courses)

    def test_prerequisite_failure_retains_unknown_candidates(self):
        def fail(query, response):
            if query.table_name == "courserule":
                raise RuntimeError("private SDK error")
            return response

        self.client.hook = fail
        result = planning.run_degree_plan(self.draft, ["CS 110"], core_ii_candidates=True, client=self.client)
        eligibility = result.handoff["course_eligibility"]
        self.assertEqual(eligibility["evaluation_status"], "UNAVAILABLE")
        self.assertTrue(result.handoff["flexible_candidates"]["candidate_course_codes"])
        self.assertEqual({c["status"] for c in eligibility["courses"]}, {"UNKNOWN"})
        self.assertNotIn("private SDK error", json.dumps(result.handoff))

    def test_bad_credit_evidence_fails_before_retrieval(self):
        for credits in ({"cs110": 3, "CS 110": 3}, {"cs110": float("nan")}, {"cs110": True}):
            with self.subTest(credits=credits), self.assertRaises(planning.PreviewError):
                planning.run_degree_plan(self.draft, ["cs110"], reported_credits=credits,
                                         core_ii_candidates=True, client=self.client)
        self.assertEqual(self.client.calls, [])
        self.read.assert_not_called()
