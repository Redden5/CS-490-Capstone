"""Degree preview/v1 characterization and opt-in v2 tests; no live connections."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.scripts import preview_degree_audit as audit
from backend.services import planning_eligibility as eligibility
from backend.services import prerequisite_loader as loader
from backend.services.prerequisite_evaluator import EvaluationInputError, _code
from backend.tests.test_prerequisite_loader import SelectClient, fixture


DATA = Path(__file__).resolve().parents[1] / "scripts" / "data" / "degree_requirements"
DRAFT = DATA / "resolved" / "computer_science_bs_resolved.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def unavailable(handoff, coursework):
    return eligibility.unavailable_candidate_prerequisites(
        [c["course_code"] for c in handoff["course_eligibility"]["courses"]], coursework,
        catalog_year=handoff["degree"]["catalog_year"], reason="TEST_UNAVAILABLE",
    )


class CompletedInputTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "completed.json"

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8-sig")
        return self.path

    def test_legacy_csv_and_json_wrapper_are_sorted_distinct(self):
        self.write({"completed_courses": ["cs110", "MTH 229", "CS 110"]})
        self.assertEqual(audit.read_completed_courses("cs120, CS 110,", self.path),
                         ["CS 110", "CS 120", "MTH 229"])
        self.assertEqual(audit.read_completed_courses(None, None), [])

    def test_list_format_and_structured_record_normalization(self):
        self.write(["cs110", {"course_code": "CS120", "grade": " a "},
                    {"course_code": "MTH 229", "grade": None, "equivalency_verified": False}])
        records = audit.read_completed_coursework(None, self.path)
        self.assertEqual(records[1], {"course_code": "CS 120", "grade": "A"})
        self.assertFalse(records[2]["equivalency_verified"])
        self.assertEqual(audit.coursework_codes(records), ["CS 110", "CS 120", "MTH 229"])

    def test_conflicting_and_identical_records_survive_reader(self):
        records = [{"course_code": "CS 110", "grade": "A"},
                   {"course_code": "CS 110", "grade": "A"}, {"course_code": "CS 110", "grade": "F"}]
        self.write(records)
        result = audit.read_completed_coursework("CS 110", self.path)
        self.assertEqual(result, ["CS 110", *records])
        self.assertEqual(audit.coursework_codes(result), ["CS 110"])

    def test_malformed_records_are_explicit_errors(self):
        values = [None, {}, 42, [None], [{"grade": "A"}],
                  [{"course_code": "CS 110", "grade": 4}],
                  [{"course_code": "CS 110", "grade": []}],
                  [{"course_code": "CS 110", "credits": 3}],
                  [{"course_code": "CS 110", "equivalency_verified": "yes"}]]
        for value in values:
            with self.subTest(value=value), self.assertRaises(audit.PreviewError):
                audit.read_completed_coursework(None, self.write(value))

    def test_invalid_json_is_explicit_error(self):
        self.path.write_text("{", encoding="utf-8")
        with self.assertRaises(audit.PreviewError):
            audit.read_completed_coursework(None, self.path)

    def test_unsupported_grade_string_is_preserved_as_evidence(self):
        self.write([{"course_code": "CS 110", "grade": "p"}])
        self.assertEqual(audit.read_completed_coursework(None, self.path)[0]["grade"], "P")

    def test_catalog_suffixes_and_hyphens_match_evaluator(self):
        for raw in ("bsc120lh", "MUS 123-1", "MTH229H", "CS 110", "CS\t110"):
            with self.subTest(raw=raw):
                self.assertEqual(audit.canonical_course_code(raw), _code(raw))
        self.assertNotEqual(audit.canonical_course_code("BSC120LH"), "BSC 120")

    def test_legacy_one_digit_codes_preserved_but_opt_in_fails_explicitly(self):
        self.assertEqual(audit.read_completed_courses("CS1", None), ["CS 1"])
        with self.assertRaises(EvaluationInputError):
            eligibility.validate_candidate_evidence([], ["CS 1"], catalog_year="2026-2027")
        for raw in ("CS 110 OR 120", "CS", "110", "CS 110/120"):
            with self.assertRaises(audit.PreviewError):
                audit.canonical_course_code(raw)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.preview = read(DATA / "audit_preview.json")
        self.handoff = audit.build_planning_handoff(self.preview)

    def test_saved_v1_fixture_matches_builder_exactly(self):
        self.assertEqual(self.handoff, read(DATA / "planning_handoff.json"))
        self.assertEqual(self.handoff["schema_version"], "1.0.0")
        self.assertEqual(len(self.handoff["requirements"]), 39)
        self.assertEqual(len(self.handoff["course_eligibility"]["courses"]), 30)
        self.assertEqual(self.handoff["course_eligibility"]["evaluation_status"], "NOT_EVALUATED")

    def test_apply_after_finalization_versions_without_mutating_progress(self):
        before = deepcopy(self.handoff)
        result = unavailable(self.handoff, self.preview["completed_course_codes_entered"])
        output = audit.apply_prerequisite_results(self.handoff, result)
        self.assertEqual(self.handoff, before)
        self.assertEqual(output["schema_version"], "2.0.0")
        self.assertEqual(output["summary"], before["summary"])
        self.assertEqual(output["distinct_course_credit_evidence"], before["distinct_course_credit_evidence"])
        for old, new in zip(before["requirements"], output["requirements"]):
            stripped = {k: v for k, v in new.items() if k != "prerequisite_evaluation_status"}
            stripped["prerequisites_evaluated"] = False
            self.assertEqual(stripped, old)
        self.assertEqual([c["course_code"] for c in output["course_eligibility"]["courses"]],
                         [c["course_code"] for c in before["course_eligibility"]["courses"]])
        output["course_eligibility"]["courses"][0]["loading"]["issues"].append("mutation")
        self.assertNotIn("mutation", str(result))

    def test_completed_evaluation_does_not_mean_met(self):
        result = unavailable(self.handoff, [])
        result["evaluation_status"] = "COMPLETED"
        for course in result["courses"]:
            course["loading"]["data_complete"] = True
        output = audit.apply_prerequisite_results(self.handoff, result)
        evaluated = [r for r in output["requirements"] if r["prerequisites_evaluated"]]
        self.assertTrue(evaluated)
        self.assertTrue(all(r["prerequisite_evaluation_status"] == "COMPLETED" for r in evaluated))
        self.assertTrue(all(c["status"] == "UNKNOWN" for c in output["course_eligibility"]["courses"]))
        self.assertFalse(output["course_eligibility"]["registration_eligibility_determined"])

    def test_choice_groups_and_unenumerated_attribute_categories_stay_intact(self):
        output = audit.apply_prerequisite_results(self.handoff, unavailable(self.handoff, []))
        self.assertEqual(output["candidate_option_coverage"], self.handoff["candidate_option_coverage"])
        self.assertFalse(output["candidate_option_coverage"]["unmet_attribute_option_lists_enumerated"])
        for old, new in zip(self.handoff["requirements"], output["requirements"]):
            if old["kind"] == "CHOOSE_N":
                self.assertEqual(new["course_options"], old["course_options"])
            if new["prerequisite_evaluation_status"] == "CANDIDATES_NOT_ENUMERATED":
                self.assertFalse(new["prerequisites_evaluated"])
                self.assertEqual(new["attribute_filter"], old["attribute_filter"])
        self.assertTrue(any(r["prerequisite_evaluation_status"] == "CANDIDATES_NOT_ENUMERATED"
                            for r in output["requirements"]))
        self.assertIn("query the catalog-year courseattribute", " ".join(output["warnings"]))

    def test_unused_or_diagnostics_never_expand_candidates(self):
        result = unavailable(self.handoff, [])
        result["courses"][0]["missing_courses"] = ["CS 999"]
        result["courses"][0]["status"] = "MET"
        output = audit.apply_prerequisite_results(self.handoff, result)
        self.assertNotIn("CS 999", [c["course_code"] for c in output["course_eligibility"]["courses"]])
        self.assertNotIn("CS 999", str(output["requirements"]))

    def test_mismatched_results_are_programming_errors(self):
        for alteration in ("year", "missing", "duplicate"):
            result = unavailable(self.handoff, [])
            if alteration == "year":
                result["catalog_year"] = "2025-2026"
            elif alteration == "missing":
                result["courses"].pop()
            else:
                result["courses"].append(result["courses"][0])
            with self.assertRaises(ValueError):
                audit.apply_prerequisite_results(self.handoff, result)

    def test_grades_do_not_change_preview_or_create_retake_candidates(self):
        draft = read(DRAFT)
        baseline = audit.evaluate_preview(draft, ["CS 110"])
        failed = audit.evaluate_preview(draft, audit.coursework_codes([{"course_code": "CS 110", "grade": "F"}]))
        self.assertEqual(failed, baseline)
        self.assertEqual(baseline["summary"]["ready_required_course_occurrences_matched"], 1)
        handoff = audit.build_planning_handoff(failed)
        self.assertNotIn("CS 110", [c["course_code"] for c in handoff["course_eligibility"]["courses"]])
        result = audit.apply_prerequisite_results(handoff, unavailable(handoff, []))
        self.assertIn("including reported failures", " ".join(result["warnings"]))


class CliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["preview_degree_audit", "--input", str(DRAFT), *map(str, args)]), \
                redirect_stdout(out), redirect_stderr(err):
            status = audit.main()
        return status, out.getvalue(), err.getvalue()

    def test_disabled_mode_no_prerequisite_io_and_exact_legacy_output(self):
        preview, handoff = self.directory / "audit.json", self.directory / "planning.json"
        with patch.object(audit, "fetch_planning_eligibility", side_effect=AssertionError("not opt-in")), \
                patch.object(audit, "_create_prerequisite_client", side_effect=AssertionError("no SDK")), \
                patch.object(Path, "read_bytes", side_effect=AssertionError("no source reads")):
            status, out, err = self.run_cli("--completed", "CS110", "--output", preview, "--planning-output", handoff)
        baseline = audit.evaluate_preview(read(DRAFT), ["CS 110"])
        self.assertEqual(status, 0, err)
        self.assertEqual(read(preview), baseline)
        self.assertEqual(read(handoff), audit.build_planning_handoff(baseline))
        self.assertNotIn("Prerequisite evidence:", out)

    def test_opt_in_preview_and_handoff_receive_same_results_with_grades(self):
        input_path = self.directory / "completed.json"
        records = [{"course_code": "CS 110", "grade": "A"}, "CS 120"]
        input_path.write_text(json.dumps(records), encoding="utf-8")
        preview, handoff = self.directory / "audit.json", self.directory / "planning.json"
        with patch.object(audit, "fetch_planning_eligibility", side_effect=unavailable) as fetch:
            status, out, err = self.run_cli("--completed-file", input_path, "--evaluate-prerequisites",
                                            "--output", preview, "--planning-output", handoff)
        self.assertEqual(status, 0, err)
        self.assertEqual(fetch.call_args.args[1], records)
        self.assertEqual(read(handoff)["schema_version"], "2.0.0")
        self.assertEqual(read(handoff)["course_eligibility"], read(preview)["course_eligibility"])
        self.assertEqual(read(preview)["summary"], audit.evaluate_preview(read(DRAFT), ["CS 110", "CS 120"])["summary"])
        self.assertIn("WARNING", out)
        self.assertIn("UNAVAILABLE", out)

    def test_opt_in_does_not_require_output_path(self):
        with patch.object(audit, "fetch_planning_eligibility", side_effect=unavailable) as fetch:
            status, out, err = self.run_cli("--evaluate-prerequisites")
        self.assertEqual(status, 0, err)
        fetch.assert_called_once()
        self.assertIn("Prerequisite evidence:", out)

    def test_cli_real_loader_and_evaluator_with_select_only_fixture(self):
        catalog, source, tables = fixture()
        input_path = self.directory / "completed.json"
        records = [{"course_code": "CS 110", "grade": "A"}, "CS 120"]
        input_path.write_text(json.dumps(records), encoding="utf-8")
        output = self.directory / "handoff.json"
        client = SelectClient(tables)
        with patch.multiple(loader, _CATALOG_SHA256=hashlib.sha256(catalog).hexdigest(),
                            _PREREQUISITE_SHA256=hashlib.sha256(source).hexdigest()), \
                patch.object(Path, "read_bytes", side_effect=[catalog, source]), \
                patch.object(audit, "_create_prerequisite_client", return_value=client), \
                patch.object(eligibility, "load_prerequisite_rules", wraps=loader.load_prerequisite_rules) as load:
            status, out, err = self.run_cli("--completed-file", input_path, "--evaluate-prerequisites",
                                            "--planning-output", output)
        self.assertEqual(status, 0, err)
        load.assert_called_once()
        result = read(output)
        self.assertEqual(result["schema_version"], "2.0.0")
        self.assertEqual(result["course_eligibility"]["evaluation_status"], "PARTIAL")
        courses = {c["course_code"]: c for c in result["course_eligibility"]["courses"]}
        self.assertEqual(courses["CS 210"]["status"], "MET")
        self.assertTrue(courses["CS 210"]["loading"]["data_complete"])
        self.assertTrue(any(c["status"] == "UNKNOWN" and not c["loading"]["data_complete"]
                            for c in courses.values()))
        baseline = audit.build_planning_handoff(audit.evaluate_preview(read(DRAFT), ["CS 110", "CS 120"]))
        self.assertEqual(result["summary"], baseline["summary"])
        self.assertEqual(sorted(courses), [c["course_code"] for c in baseline["course_eligibility"]["courses"]])
        self.assertTrue(client.calls)

    def test_partial_loading_does_not_claim_requirement_evaluated(self):
        handoff = audit.build_planning_handoff(audit.evaluate_preview(read(DRAFT), []))
        result = unavailable(handoff, [])
        result["evaluation_status"] = "PARTIAL"
        result["courses"][0]["loading"]["data_complete"] = True
        output = audit.apply_prerequisite_results(handoff, result)
        required = [r for r in output["requirements"] if r["kind"] == "REQUIRED_COURSE"
                    and r["remaining_required_course_codes"]]
        self.assertTrue(any(r["prerequisite_evaluation_status"] == "INCOMPLETE" for r in required))
        self.assertTrue(all(not r["prerequisites_evaluated"] for r in required
                            if r["prerequisite_evaluation_status"] == "INCOMPLETE"))

    def test_source_file_failure_is_sanitized_without_client_creation(self):
        handoff = audit.build_planning_handoff(audit.evaluate_preview(read(DRAFT), []))
        with patch.object(Path, "read_bytes", side_effect=OSError("private secret")), \
                patch.object(audit, "_create_prerequisite_client") as client:
            result = audit.fetch_planning_eligibility(handoff, [])
        client.assert_not_called()
        self.assertEqual(result["evaluation_status"], "UNAVAILABLE")
        self.assertEqual(result["issues"], [{"code": "SOURCE_ARTIFACTS_UNAVAILABLE"}])
        self.assertNotIn("private secret", str(result))

    def test_client_configuration_and_dependency_failure_are_sanitized(self):
        handoff = audit.build_planning_handoff(audit.evaluate_preview(read(DRAFT), []))
        for error in (ImportError("secret"), RuntimeError("secret"), ValueError("secret"), OSError("secret")):
            with self.subTest(error=type(error)), patch.object(Path, "read_bytes", return_value=b"fixture"), \
                    patch.object(audit, "_create_prerequisite_client", side_effect=error):
                result = audit.fetch_planning_eligibility(handoff, [])
            self.assertEqual(result["evaluation_status"], "UNAVAILABLE")
            self.assertNotIn("secret", str(result))

    def test_empty_candidates_skip_sources_and_client(self):
        handoff = {"degree": {"catalog_year": "2026-2027"}, "course_eligibility": {"courses": []}}
        with patch.object(Path, "read_bytes", side_effect=AssertionError("no source read")), \
                patch.object(audit, "_create_prerequisite_client", side_effect=AssertionError("no client")):
            result = audit.fetch_planning_eligibility(handoff, [])
        self.assertEqual(result["evaluation_status"], "NO_CANDIDATES")

    def test_fetch_passes_draft_year_and_both_artifacts_to_single_adapter_call(self):
        handoff = {"degree": {"catalog_year": "2025-2026"},
                   "course_eligibility": {"courses": [{"course_code": "CS 210"}]}}
        client = object()
        with patch.object(Path, "read_bytes", side_effect=[b"catalog", b"prerequisites"]), \
                patch.object(audit, "_create_prerequisite_client", return_value=client), \
                patch.object(eligibility, "evaluate_candidate_prerequisites", return_value={}) as evaluate:
            audit.fetch_planning_eligibility(handoff, ["CS 110"])
        evaluate.assert_called_once_with(client, ["CS 210"], ["CS 110"], catalog_year="2025-2026",
                                         source_catalog_csv=b"catalog", source_prerequisite_csv=b"prerequisites")

    def test_environment_factory_is_lazy_and_preserves_process_environment(self):
        dotenv = SimpleNamespace(load_dotenv=lambda **kwargs: None)
        with patch.dict(sys.modules, {"dotenv": dotenv}), patch.object(dotenv, "load_dotenv") as load, \
                patch("backend.scripts.prerequisite_supabase.create_api_client", return_value="client") as create:
            self.assertEqual(audit._create_prerequisite_client(), "client")
        create.assert_called_once_with()
        self.assertFalse(load.call_args.kwargs["override"])
        self.assertEqual(load.call_args.kwargs["dotenv_path"].name, ".env")

    def test_malformed_student_input_is_failure_before_loading(self):
        path = self.directory / "bad.json"
        path.write_text('[{"course_code":"CS 110","grade":4}]', encoding="utf-8")
        with patch.object(audit, "fetch_planning_eligibility") as fetch:
            status, out, err = self.run_cli("--completed-file", path, "--evaluate-prerequisites")
        self.assertEqual(status, 1)
        self.assertIn("grade must be", err)
        fetch.assert_not_called()

    def test_programming_errors_do_not_become_unavailable(self):
        with patch.object(audit, "fetch_planning_eligibility", side_effect=KeyError("bug")):
            with self.assertRaises(KeyError):
                self.run_cli("--evaluate-prerequisites")


if __name__ == "__main__":
    unittest.main()
