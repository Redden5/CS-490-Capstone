"""Offline Stage 3 integration using the real loader/evaluator and SELECT-only fake."""

from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

from backend.services import planning_eligibility as planning
from backend.services import prerequisite_loader as loader
from backend.services.prerequisite_evaluator import EvaluationInputError
from backend.tests.test_prerequisite_loader import SelectClient, fixture, YEAR


class PlanningEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.catalog, self.raw, self.tables = fixture()
        pins = patch.multiple(loader, _CATALOG_SHA256=hashlib.sha256(self.catalog).hexdigest(),
                              _PREREQUISITE_SHA256=hashlib.sha256(self.raw).hexdigest())
        pins.start()
        self.addCleanup(pins.stop)
        self.client = SelectClient(self.tables, cap=1)

    def evaluate(self, codes=None, coursework=None, **kwargs):
        return planning.evaluate_candidate_prerequisites(
            self.client, ["CS 210"] if codes is None else codes,
            [{"course_code": "CS 110", "grade": "A"}, "CS 120"] if coursework is None else coursework,
            catalog_year=kwargs.pop("catalog_year", YEAR),
            source_catalog_csv=kwargs.pop("source_catalog_csv", self.catalog),
            source_prerequisite_csv=kwargs.pop("source_prerequisite_csv", self.raw), **kwargs,
        )

    def test_real_components_met_and_unused_or_diagnostics(self):
        result = self.evaluate()
        item = result["courses"][0]
        self.assertEqual(result["evaluation_status"], "COMPLETED")
        self.assertEqual(item["status"], "MET")
        self.assertIn("MTH 220", item["missing_courses"])
        self.assertFalse(item["registration_eligibility_determined"])
        self.assertFalse(result["grades_used_for_degree_progress"])
        self.assertEqual(result["source_verification"], "HASH_VERIFIED_SOURCE")
        self.assertEqual(result["retrieval_status"], "VISIBLE_READS_AGREE_NOT_ATOMIC")
        self.assertEqual(result["summary"], {"MET": 1, "UNMET": 0, "UNKNOWN": 0})
        self.assertGreater(len(self.client.calls), 10)  # Pagination and repeated graph reads.

    def test_batch_once_and_deterministic_deduplicated_candidates(self):
        with patch.object(planning, "load_prerequisite_rules", wraps=loader.load_prerequisite_rules) as load:
            result = self.evaluate(["CS230", "cs 210", "CS 210", "CS 220"])
        load.assert_called_once()
        self.assertEqual(load.call_args.args[1], ["CS 210", "CS 220", "CS 230"])
        self.assertEqual([c["course_code"] for c in result["courses"]], ["CS 210", "CS 220", "CS 230"])
        self.assertEqual(result["summary"], {"MET": 1, "UNMET": 0, "UNKNOWN": 2})

    def test_grades_below_minimum_are_unmet(self):
        result = self.evaluate(coursework=[{"course_code": "CS 110", "grade": "D"}, "CS 120"])
        self.assertEqual(result["courses"][0]["status"], "UNMET")

    def test_missing_grade_legacy_and_unsupported_grades_remain_unknown(self):
        for evidence in (["CS 110", "CS 120"], [{"course_code": "CS 110", "grade": "A+"}, "CS 120"]):
            with self.subTest(evidence=evidence):
                result = self.evaluate(coursework=evidence)
                self.assertEqual(result["courses"][0]["status"], "UNKNOWN")
                self.assertTrue(result["courses"][0]["insufficient_grade_evidence"])

    def test_repeated_evidence_not_silently_resolved(self):
        item = self.evaluate(coursework=["CS 110", {"course_code": "CS 110", "grade": "A"}, "CS 120"])["courses"][0]
        self.assertEqual(item["status"], "UNKNOWN")
        self.assertIn("REPEATED_COURSE_EVIDENCE_CONFLICT", str(item["insufficient_grade_evidence"]))

    def test_complete_unresolved_parent_and_corequisite_are_unknown(self):
        result = self.evaluate(["CS 220", "CS 230"])
        self.assertEqual(result["evaluation_status"], "COMPLETED")
        for item in result["courses"]:
            self.assertTrue(item["loading"]["data_complete"])
            self.assertEqual(item["status"], "UNKNOWN")
        coreq = result["courses"][0]
        # The existing parser applies the corequisite review flag to both
        # parents. The adapter must preserve that source readiness policy.
        self.assertEqual(coreq["rule_groups"]["PREREQUISITE"]["status"], "UNKNOWN")
        self.assertEqual(coreq["rule_groups"]["COREQUISITE"]["status"], "UNKNOWN")
        self.assertEqual(len(coreq["loading"]["rule_provenance"]), 2)

    def test_provenance_readiness_and_grade_evidence_retained(self):
        item = self.evaluate()["courses"][0]
        parent = item["loading"]["rule_provenance"][0]
        for field in ("rule_id", "course_id", "catalog_year_id", "raw_text", "source_url",
                      "machine_checkable", "parse_status", "review_flags", "content_sha256", "source_metadata"):
            self.assertIn(field, parent)
        self.assertTrue(item["loading"]["clause_provenance"])
        atom = item["rules"][0]["clauses"][0]["conditions"][0]
        self.assertEqual(atom["reported_grades"], ["A"])
        self.assertIn("required_course_id", atom)
        self.assertIn("rule_course_id", atom)

    def test_concurrent_evidence_is_explicit_not_candidate_pool(self):
        records = [{"course_code": "CS 110", "grade": "A"}]
        for concurrent, expected in ((None, "UNKNOWN"), ([], "UNMET"), (["MTH 220"], "MET")):
            with self.subTest(concurrent=concurrent):
                result = self.evaluate(["CS 210", "MTH 220"], records, concurrent_courses=concurrent)
                self.assertEqual(result["courses"][0]["status"], expected)

    def test_no_metadata_is_incomplete_not_met(self):
        result = self.evaluate(["CS 999"])
        self.assertEqual(result["evaluation_status"], "PARTIAL")
        item = result["courses"][0]
        self.assertEqual(item["status"], "UNKNOWN")
        self.assertIn("NO_METADATA_IS_NOT_NO_PREREQUISITES", item["loading"]["issues"])

    def test_hidden_relationship_and_unsupported_conditions_keep_diagnostics(self):
        for defect in ("atom", "condition"):
            with self.subTest(defect=defect):
                client = SelectClient(self.tables)
                if defect == "atom":
                    client.tables["rulecourse"].pop(0)
                else:
                    client.tables["ruleclause"][0]["condition_text"] = "advisor consent"
                self.client = client
                item = self.evaluate()["courses"][0]
                self.assertFalse(item["loading"]["data_complete"])
                self.assertEqual(item["status"], "UNKNOWN")
                self.assertEqual(item["rules"], [])
                self.assertTrue(item["loading"]["issues"])
                self.assertTrue(item["loading"]["rule_provenance"])

    def test_missing_required_course_is_unknown(self):
        self.client.tables["course"] = [r for r in self.client.tables["course"] if r["course_code"] != "CS 110"]
        item = self.evaluate()["courses"][0]
        self.assertEqual(item["status"], "UNKNOWN")
        self.assertIn("REQUIRED_COURSE_MISSING_OR_HIDDEN", item["loading"]["issues"])

    def test_missing_or_changed_source_never_claims_completeness(self):
        for catalog, raw in ((None, None), (self.catalog + b"\n", self.raw)):
            result = self.evaluate(source_catalog_csv=catalog, source_prerequisite_csv=raw)
            self.assertEqual(result["evaluation_status"], "PARTIAL")
            self.assertEqual(result["courses"][0]["status"], "UNKNOWN")

    def test_year_is_not_substituted(self):
        result = self.evaluate(catalog_year="2025-2026")
        self.assertEqual(result["catalog_year"], "2025-2026")
        self.assertEqual(result["source_verification"], "SOURCE_YEAR_UNSUPPORTED")
        self.assertEqual(result["courses"][0]["status"], "UNKNOWN")
        self.assertFalse(result["courses"][0]["loading"]["data_complete"])

    def test_transport_failure_discards_partial_reads_and_sanitizes(self):
        def fail(query, response):
            if query.table_name == "rulecourse":
                raise RuntimeError("secret-credential https://private.example/")
            return response
        self.client = SelectClient(self.tables, hook=fail)
        result = self.evaluate(["CS 210", "CS 230"])
        self.assertEqual(result["evaluation_status"], "UNAVAILABLE")
        self.assertEqual(result["issues"], [{"code": "QUERY_ERROR", "table": "rulecourse"}])
        self.assertNotIn("secret-credential", str(result))
        self.assertTrue(all(c["status"] == "UNKNOWN" and not c["loading"]["data_complete"]
                            for c in result["courses"]))

    def test_empty_candidates_do_not_load(self):
        with patch.object(planning, "load_prerequisite_rules") as load:
            result = self.evaluate([])
        load.assert_not_called()
        self.assertEqual(result["evaluation_status"], "NO_CANDIDATES")
        self.assertEqual(result["courses"], [])

    def test_malformed_evidence_fails_before_loading_even_empty(self):
        for records in ("CS 110", [{"course_code": "CS 110", "grade": 4}], [{"course_code": "CS 110", "extra": 1}]):
            with self.subTest(records=records), patch.object(planning, "load_prerequisite_rules") as load:
                with self.assertRaises(EvaluationInputError):
                    self.evaluate([], records)
                load.assert_not_called()

    def test_malformed_request_context_fails(self):
        for kwargs in ({"catalog_year": ""}, {"concurrent_courses": "MTH 220"}):
            with self.assertRaises(EvaluationInputError):
                self.evaluate(**kwargs)
        with self.assertRaises(EvaluationInputError):
            self.evaluate(["CS 1"])  # Legacy audit identity outside supported catalog grammar.

    def test_unexpected_adapter_programming_error_propagates(self):
        with patch.object(planning, "load_prerequisite_rules", side_effect=KeyError("bug")):
            with self.assertRaises(KeyError):
                self.evaluate()

    def test_inputs_and_outputs_do_not_alias(self):
        records = [{"course_code": "CS 110", "grade": "A"}, "CS 120"]
        before = deepcopy(records)
        result = self.evaluate(coursework=records)
        self.assertEqual(records, before)
        result["courses"][0]["loading"]["rule_provenance"][0]["review_flags"].append("changed")
        self.assertEqual(self.client.tables["courserule"][0]["review_flags"], [])

    def test_candidate_provider_is_not_tied_to_degree_structure(self):
        # A future category lookup can pass approved codes through this exact API.
        self.assertEqual(self.evaluate(["CS 210"])["courses"][0]["status"], "MET")
        self.assertNotIn("requirements", self.evaluate(["CS 210"]))


if __name__ == "__main__":
    unittest.main()
