"""Stage 4B CLI integration: real audit/providers/evaluator, SELECT-only fake SDK."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from backend.scripts import preview_degree_audit as audit
from backend.services import degree_planning
from backend.scripts.parse_prerequisites import parse_rows
from backend.services import flexible_candidates as flexible
from backend.services import planning_eligibility as eligibility
from backend.services import prerequisite_loader as loader
from backend.tests.test_flexible_candidates import fixture as attribute_fixture, csv_bytes as attribute_csv
from backend.tests.test_prerequisite_loader import tables_from_source, csv_bytes


ROOT = Path(__file__).resolve().parents[2]
DRAFT = ROOT / "backend/scripts/data/degree_requirements/resolved/computer_science_bs_resolved.json"
YEAR = "2026-2027"
HUM, SOCIAL, FINE = "Core II Humanities", "Core II Social Sciences", "Core II Fine Arts"


def fixture():
    attributes, source = attribute_fixture()
    codes = sorted({c["course_code"] for c in attributes["course"]} | {"CS 110", "CS 120", "CS 210"})
    url = "https://catalog.marshall.edu/undergraduate/courses-az/cs/"
    metadata = [{"course_code": code, "catalog_year": YEAR, "source_url": url,
                 "field_label": "pre-req", "requirement_text": text} for code, text in [
        ("CS 210", "CS 110 with a minimum grade of C"),
        ("PHL 101", "CS 110 with a minimum grade of C"),
        ("HST 101", "Consent of instructor."),
        ("HST 200", "CS 110"),
        ("ART 112", "CS 120"),
        ("ART 200", "CS 110 or CS 120"),
        ("ART 220", "CS 110"),
    ]]
    tables = tables_from_source(codes, parse_rows(metadata, set(codes)))
    by_code = {r["course_code"]: r for r in attributes["course"]}
    ids = {r["course_code"]: r["course_id"] for r in tables["course"]}
    old_codes = {r["course_id"]: r["course_code"] for r in attributes["course"]}
    for course in tables["course"]:
        stored_id = course["course_id"]
        course.update(by_code.get(course["course_code"], {
            "course_title": "Fixture course", "min_credit_hours": 3, "max_credit_hours": 3,
        }))
        course["course_id"] = stored_id
        course["active_status"] = True
    tables["attribute"] = attributes["attribute"]
    tables["courseattribute"] = [{**r, "course_id": ids[old_codes[r["course_id"]]]}
                                 for r in attributes["courseattribute"]]
    # Synthetic cross-provider membership exercises explicit/flexible deduplication.
    tables["courseattribute"].append({"course_id": ids["CS 210"], "attribute_id": 1,
        "catalog_year_id": 7, "source_url": url, "observed_at": "2026-10-14T18:03:02+00:00"})
    source.append({"course_code": "CS 210", "attribute_name": HUM, "source_url": url,
        "catalog_year": YEAR, "catalog_level": "undergraduate", "observed_at_utc": "2026-10-14T18:03:02+00:00"})
    return tables, attribute_csv(source), csv_bytes([{"course_code": c} for c in codes]), csv_bytes(metadata)


class Query:
    def __init__(self, client, table):
        self.client, self.table_name = client, table
        self.equals, self.within, self.nulls, self.orders = {}, {}, [], []

    def select(self, columns, *, count=None):
        self.columns, self.count = columns.split(","), count
        return self

    def eq(self, key, value):
        self.equals[key] = value
        return self

    def in_(self, key, values):
        if not values: raise AssertionError("Empty IN")
        self.within[key] = list(values)
        return self

    def is_(self, key, value):
        if value != "null": raise AssertionError("Unexpected null filter")
        self.nulls.append(key)
        return self

    def order(self, key):
        self.orders.append(key)
        return self

    def range(self, start, end):
        self.start, self.end = start, end
        return self

    def execute(self):
        self.client.calls.append(self)
        rows = [r for r in self.client.tables[self.table_name]
                if all(r[k] == v for k, v in self.equals.items())
                and all(r[k] in v for k, v in self.within.items())
                and all(r[k] is None for k in self.nulls)]
        rows = sorted(rows, key=lambda r: tuple(r[k] for k in self.orders))
        response = SimpleNamespace(count=len(rows), data=deepcopy([
            {k: r[k] for k in self.columns} for r in rows[self.start:self.end + 1]
        ]))
        return self.client.hook(self, response) if self.client.hook else response


class Client:
    def __init__(self, tables):
        self.tables, self.calls, self.hook = deepcopy(tables), [], None

    def table(self, name):
        if name not in {"catalogyear", "attribute", "courseattribute", "course", "courserule", "ruleclause", "rulecourse"}:
            raise AssertionError("Unexpected table")
        return Query(self, name)


class CoreIIPlanningTests(unittest.TestCase):
    def setUp(self):
        tables, attributes, catalog, prerequisites = fixture()
        self.client = Client(tables)
        self.files = {"catalog_attributes.csv": attributes, "courses.csv": catalog,
                      "catalog_prerequisites.csv": prerequisites}
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.preview_path, self.handoff_path = self.directory / "audit.json", self.directory / "handoff.json"
        self.sdk = SimpleNamespace(ClientOptions=Mock(), create_client=Mock(return_value=self.client))
        patches = [
            patch.object(flexible, "_SOURCE_SHA256", hashlib.sha256(attributes).hexdigest()),
            patch.multiple(loader, _CATALOG_SHA256=hashlib.sha256(catalog).hexdigest(),
                           _PREREQUISITE_SHA256=hashlib.sha256(prerequisites).hexdigest()),
            patch.object(Path, "read_bytes", autospec=True, side_effect=lambda path: self.files[path.name]),
            patch.dict(os.environ, {"SUPABASE_URL": "https://fixture.invalid", "SUPABASE_SECRET_KEY": "fixture-only"},
                       clear=True),
            patch.dict(sys.modules, {"dotenv": SimpleNamespace(load_dotenv=Mock()), "supabase": self.sdk}),
        ]
        for context in patches:
            context.start()
            self.addCleanup(context.stop)

    def run_cli(self, *flags, records=None, output=True):
        path = self.directory / "completed.json"
        path.write_text(json.dumps(records if records is not None else [{"course_code": "CS 110", "grade": "A"}]), encoding="utf-8")
        args = ["preview_degree_audit", "--input", str(DRAFT), "--completed-file", str(path), *flags]
        if output:
            args.extend(["--output", str(self.preview_path), "--planning-output", str(self.handoff_path)])
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", args), redirect_stdout(stdout), redirect_stderr(stderr):
            code = audit.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def outputs(self):
        return (json.loads(self.preview_path.read_text(encoding="utf-8")),
                json.loads(self.handoff_path.read_text(encoding="utf-8")))

    def successful(self, *flags, **kwargs):
        status, out, err = self.run_cli(*flags, **kwargs)
        self.assertEqual(status, 0, err)
        self.assertEqual(err, "")
        return self.outputs(), out

    def test_cli_service_role_fallback_builds_one_client_for_core_ii(self):
        with patch.dict(os.environ, {"SUPABASE_SECRET_KEY": "", "SUPABASE_SERVICE_ROLE_KEY": "fixture-legacy"}):
            (_, handoff), _ = self.successful("--core-ii-candidates")
        self.sdk.create_client.assert_called_once_with(
            "https://fixture.invalid", "fixture-legacy", options=self.sdk.ClientOptions.return_value,
        )
        self.assertEqual(handoff["flexible_candidates"]["generation_status"], "COMPLETED")
        self.assertGreater(handoff["course_eligibility"]["summary"]["MET"], 0)

    def test_opt_in_full_workflow_combines_real_services_once(self):
        with patch.object(eligibility, "load_prerequisite_rules", wraps=loader.load_prerequisite_rules) as load, \
                patch.object(eligibility, "evaluate_candidate_prerequisites", wraps=eligibility.evaluate_candidate_prerequisites) as evaluate:
            (preview, handoff), out = self.successful("--core-ii-candidates")
        load.assert_called_once()
        evaluate.assert_called_once()
        self.assertEqual(handoff["schema_version"], "2.1.0")
        self.assertEqual(handoff["flexible_candidates"], preview["flexible_candidates"])
        self.assertEqual(handoff["course_eligibility"], preview["course_eligibility"])
        self.assertEqual(handoff["flexible_candidates"]["generation_status"], "COMPLETED")
        self.assertEqual(handoff["flexible_candidates"]["completed_course_attribute_evidence"]["status"], "AVAILABLE")
        codes = [c["course_code"] for c in handoff["course_eligibility"]["courses"]]
        self.assertEqual(codes, sorted(set(codes)))
        self.assertEqual(codes.count("CS 210"), 1)
        self.assertEqual(load.call_args.args[1], codes)
        self.assertEqual(evaluate.call_args.args[2], [{"course_code": "CS 110", "grade": "A"}])
        self.assertIn("Core II candidates: COMPLETED", out)
        self.assertNotIn("reported failures", out)

    def test_met_unmet_unknown_and_provenance_are_preserved(self):
        (_, handoff), _ = self.successful("--core-ii-candidates")
        by_code = {c["course_code"]: c for c in handoff["course_eligibility"]["courses"]}
        self.assertEqual(by_code["PHL 101"]["status"], "MET")
        self.assertEqual(by_code["ART 112"]["status"], "UNMET")
        self.assertEqual(by_code["HST 101"]["status"], "UNKNOWN")
        self.assertEqual(by_code["ART 210"]["status"], "UNKNOWN")
        self.assertIn("RULE_NOT_MACHINE_CHECKABLE", by_code["HST 101"]["rules"][0]["reasons"])
        for requirement in handoff["flexible_candidates"]["requirements"]:
            self.assertEqual(requirement["remaining_credits_candidate"], 3)
            for candidate in requirement["candidates"]:
                code = candidate["course_code"]
                self.assertEqual(candidate["course_eligibility_reference"], code)
                self.assertEqual(candidate["prerequisite_status"], by_code[code]["status"])
                self.assertTrue(candidate["classification_sources"])
                self.assertIn("credit_fit", candidate)
        self.assertFalse(handoff["course_eligibility"]["registration_eligibility_determined"])
        self.assertTrue(by_code["PHL 101"]["loading"]["rule_provenance"])

    def test_missing_grade_is_unknown_without_affecting_progress(self):
        (graded_preview, _), _ = self.successful("--core-ii-candidates")
        (preview, handoff), _ = self.successful("--core-ii-candidates", records=["CS 110"])
        self.assertEqual(preview["rules"], graded_preview["rules"])
        self.assertEqual(preview["summary"], graded_preview["summary"])
        phl = next(c for c in handoff["course_eligibility"]["courses"] if c["course_code"] == "PHL 101")
        self.assertEqual(phl["status"], "UNKNOWN")
        self.assertTrue(phl["insufficient_grade_evidence"])

    def test_unused_or_diagnostics_do_not_expand_the_pool(self):
        (_, handoff), _ = self.successful("--core-ii-candidates")
        by_code = {c["course_code"]: c for c in handoff["course_eligibility"]["courses"]}
        self.assertEqual(by_code["ART 200"]["status"], "MET")
        self.assertIn("CS 120", by_code["ART 200"]["missing_courses"])
        flexible_codes = handoff["flexible_candidates"]["candidate_course_codes"]
        self.assertNotIn("CS 120", flexible_codes)
        self.assertNotIn("CS 120", handoff["flexible_candidates"]["course_requirement_memberships"])

    def test_cross_category_memberships_are_retained_without_credit_allocation(self):
        (_, handoff), _ = self.successful("--core-ii-candidates")
        block = handoff["flexible_candidates"]
        self.assertEqual(block["course_requirement_memberships"]["HST 101"], ["t0_row_0012", "t0_row_0013"])
        self.assertEqual(block["candidate_course_codes"].count("HST 101"), 1)
        self.assertEqual(block["allocation_status"], "NOT_ALLOCATED_NO_DOUBLE_COUNTING_ASSUMED")

    def test_completed_categories_have_no_candidates_or_attribute_candidate_queries(self):
        with patch.object(flexible, "load_attribute_candidates", wraps=flexible.load_attribute_candidates) as load:
            (_, handoff), _ = self.successful("--core-ii-candidates", records=["CS 110", "PHL 101", "HST 101", "ART 112"])
        self.assertTrue(all(call.args[1] == [] for call in load.call_args_list))
        block = handoff["flexible_candidates"]
        self.assertEqual(block["generation_status"], "NOT_NEEDED")
        self.assertEqual(block["candidate_course_codes"], [])
        self.assertTrue(all(r["generation_status"] == "SKIPPED" for r in block["requirements"]))

    def test_only_unfinished_categories_are_queried_and_completed_courses_excluded(self):
        with patch.object(flexible, "load_attribute_candidates", wraps=flexible.load_attribute_candidates) as load:
            (_, handoff), _ = self.successful("--core-ii-candidates", records=["CS 110", "PHL 101"])
        self.assertEqual(load.call_args.args[1], [FINE, SOCIAL])
        hum = next(r for r in handoff["flexible_candidates"]["requirements"] if r["category"] == HUM)
        self.assertEqual(hum["generation_status"], "SKIPPED")
        self.assertNotIn("PHL 101", [c["course_code"] for c in handoff["course_eligibility"]["courses"]])

    def test_attribute_progress_failure_is_sanitized_and_does_not_invent_shortfalls(self):
        with patch.object(degree_planning, "fetch_supabase_attribute_evidence", side_effect=audit.AttributeEvidenceUnavailable("secret-token")), \
                patch.object(flexible, "load_attribute_candidates", wraps=flexible.load_attribute_candidates) as load:
            (preview, handoff), out = self.successful("--core-ii-candidates")
        self.assertTrue(all(call.args[1] == [] for call in load.call_args_list))
        block = handoff["flexible_candidates"]
        self.assertEqual(block["generation_status"], "UNAVAILABLE")
        self.assertEqual(block["candidate_course_codes"], [])
        self.assertTrue(all(r["generation_status"] == "INSUFFICIENT_EVIDENCE" for r in block["requirements"]))
        self.assertNotIn("secret-token", json.dumps(handoff) + out)
        self.assertIn("WARNING", out)
        baseline = audit.evaluate_preview(json.loads(DRAFT.read_text()), ["CS 110"])
        self.assertEqual(preview["rules"], baseline["rules"])

    def test_real_attribute_query_failure_keeps_explicit_prerequisites(self):
        def fail(query, response):
            if query.table_name == "courseattribute": raise RuntimeError("private SDK payload")
            return response
        self.client.hook = fail
        (_, handoff), out = self.successful("--core-ii-candidates")
        self.assertEqual(handoff["flexible_candidates"]["generation_status"], "UNAVAILABLE")
        self.assertEqual(handoff["flexible_candidates"]["candidate_course_codes"], [])
        cs = next(c for c in handoff["course_eligibility"]["courses"] if c["course_code"] == "CS 210")
        self.assertEqual(cs["status"], "MET")
        self.assertNotIn("private SDK payload", out + json.dumps(handoff))

    def test_candidate_loader_failure_is_visible_without_losing_audit(self):
        def fail(query, response):
            if query.table_name == "courseattribute" and query.count == "exact":
                raise RuntimeError("private SDK payload")
            return response
        self.client.hook = fail
        (preview, handoff), out = self.successful("--core-ii-candidates")
        self.assertEqual(handoff["flexible_candidates"]["generation_status"], "UNAVAILABLE")
        self.assertEqual(handoff["flexible_candidates"]["candidate_course_codes"], [])
        self.assertTrue(any(r["candidate_progress_status"] == "CANDIDATE_NOT_MET" for r in preview["rules"]))
        self.assertNotIn("private SDK payload", out + json.dumps(handoff))

    def test_partial_membership_coverage_does_not_claim_requirement_evaluated(self):
        code_id = next(c["course_id"] for c in self.client.tables["course"] if c["course_code"] == "PHL 101")
        self.client.tables["courseattribute"] = [r for r in self.client.tables["courseattribute"] if r["course_id"] != code_id]
        (_, handoff), out = self.successful("--core-ii-candidates")
        self.assertEqual(handoff["flexible_candidates"]["generation_status"], "PARTIAL")
        hum = next(r for r in handoff["requirements"] if r["rule_id"] == "t0_row_0012")
        self.assertFalse(hum["prerequisites_evaluated"])
        self.assertFalse(hum["attribute_filter"]["eligible_catalog_course_options_included"])
        self.assertEqual(hum["prerequisite_evaluation_status"], "INCOMPLETE")
        self.assertIn("WARNING", out)

    def test_prerequisite_failure_retains_flexible_memberships_as_unknown(self):
        def fail(query, response):
            if query.table_name == "courserule": raise RuntimeError("private SDK payload")
            return response
        self.client.hook = fail
        (_, handoff), out = self.successful("--core-ii-candidates")
        self.assertEqual(handoff["course_eligibility"]["evaluation_status"], "UNAVAILABLE")
        self.assertTrue(handoff["flexible_candidates"]["candidate_course_codes"])
        self.assertTrue(all(c["status"] == "UNKNOWN" for c in handoff["course_eligibility"]["courses"]))
        self.assertTrue(all(c["prerequisite_status"] == "UNKNOWN" for r in handoff["flexible_candidates"]["requirements"] for c in r["candidates"]))
        self.assertNotIn("private SDK payload", out + json.dumps(handoff))

    def test_missing_source_artifact_leaves_flexible_pool_unavailable(self):
        def read(path):
            if path.name == "catalog_attributes.csv": raise OSError("private local path")
            return self.files[path.name]
        with patch.object(Path, "read_bytes", new=read):
            (_, handoff), out = self.successful("--core-ii-candidates")
        self.assertEqual(handoff["flexible_candidates"]["generation_status"], "UNAVAILABLE")
        self.assertEqual(handoff["flexible_candidates"]["candidate_course_codes"], [])
        self.assertNotIn("private local path", out + json.dumps(handoff))

    def test_changed_attribute_source_stays_partial_not_complete(self):
        self.files["catalog_attributes.csv"] += b"\n"
        (_, handoff), _ = self.successful("--core-ii-candidates")
        self.assertEqual(handoff["flexible_candidates"]["generation_status"], "PARTIAL")
        self.assertEqual(handoff["flexible_candidates"]["source_verification"], "SOURCE_HASH_MISMATCH")
        self.assertTrue(all(not r["coverage"]["coverage_complete"] for r in handoff["flexible_candidates"]["requirements"]))

    def test_disabled_modes_do_not_query_attribute_candidates_and_preserve_versions(self):
        for flags, version in (([], "1.0.0"), (["--evaluate-prerequisites"], "2.0.0")):
            with self.subTest(version=version), patch.object(degree_planning, "fetch_core_ii_candidates", side_effect=AssertionError("not enabled")), \
                    patch.object(flexible, "load_attribute_candidates", side_effect=AssertionError("no category queries")):
                (_, handoff), _ = self.successful(*flags)
            self.assertEqual(handoff["schema_version"], version)
            self.assertNotIn("flexible_candidates", handoff)
        self.assertFalse(any(c.table_name in ("attribute", "courseattribute") for c in self.client.calls))

    def test_legacy_v2_output_exactly_matches_existing_projection(self):
        (_, handoff), _ = self.successful("--evaluate-prerequisites")
        preview = audit.evaluate_preview(json.loads(DRAFT.read_text()), ["CS 110"])
        baseline = audit.build_planning_handoff(preview)
        expected = audit.apply_prerequisite_results(baseline, audit.fetch_planning_eligibility(
            baseline, [{"course_code": "CS 110", "grade": "A"}]))
        self.assertEqual(handoff, expected)

    def test_v21_progress_matches_existing_attribute_enabled_audit(self):
        (old_preview, old_handoff), _ = self.successful("--supabase-attributes", "--evaluate-prerequisites")
        (preview, handoff), _ = self.successful("--core-ii-candidates")
        for key in old_preview:
            if key not in ("warnings", "course_eligibility"):
                self.assertEqual(preview[key], old_preview[key], key)
        self.assertEqual(handoff["summary"], old_handoff["summary"])
        self.assertEqual(handoff["distinct_course_credit_evidence"], old_handoff["distinct_course_credit_evidence"])
        for old, new in zip(old_handoff["requirements"], handoff["requirements"]):
            adjusted = deepcopy(new)
            for key in ("prerequisites_evaluated", "prerequisite_evaluation_status"):
                adjusted[key] = old[key]
            if "attribute_filter" in old:
                adjusted["attribute_filter"]["eligible_catalog_course_options_included"] = old["attribute_filter"]["eligible_catalog_course_options_included"]
            self.assertEqual(adjusted, old)

    def test_unsupported_categories_remain_unenumerated(self):
        (_, handoff), _ = self.successful("--core-ii-candidates")
        science = next(r for r in handoff["requirements"] if r["rule_id"] == "t0_row_0011")
        self.assertEqual(science["prerequisite_evaluation_status"], "CANDIDATES_NOT_ENUMERATED")
        self.assertIn("lab_eligibility_unverified", science["remaining_policy_review_flags"])
        self.assertFalse(handoff["candidate_option_coverage"]["unmet_attribute_option_lists_enumerated"])
        self.assertIn("not enumerated", " ".join(handoff["warnings"]))

    def test_all_flags_together_still_use_single_prerequisite_batch(self):
        with patch.object(eligibility, "load_prerequisite_rules", wraps=loader.load_prerequisite_rules) as load:
            (_, handoff), _ = self.successful("--core-ii-candidates", "--supabase-attributes", "--evaluate-prerequisites")
        load.assert_called_once()
        self.assertEqual(handoff["schema_version"], "2.1.0")

    def test_opt_in_without_output_paths_still_generates_and_evaluates(self):
        with patch.object(eligibility, "load_prerequisite_rules", wraps=loader.load_prerequisite_rules) as load:
            code, out, err = self.run_cli("--core-ii-candidates", output=False)
        self.assertEqual(code, 0, err)
        self.assertIn("Core II candidates:", out)
        load.assert_called_once()

    def test_input_errors_and_programming_errors_are_not_hidden(self):
        code, _, err = self.run_cli("--core-ii-candidates", records=[{"course_code": "CS 110", "grade": 3}])
        self.assertEqual(code, 1)
        self.assertIn("grade", err)
        self.assertEqual(self.client.calls, [])
        code, _, err = self.run_cli("--core-ii-candidates", "--reported-credits", "CS 110=9")
        self.assertEqual(code, 1)
        self.assertIn("outside catalog range", err)
        with patch.object(degree_planning, "fetch_core_ii_candidates", side_effect=KeyError("bug")):
            with self.assertRaises(KeyError): self.run_cli("--core-ii-candidates")

    def test_legacy_attribute_failure_remains_an_error(self):
        with patch.object(degree_planning, "fetch_supabase_attribute_evidence", side_effect=audit.AttributeEvidenceUnavailable("Unavailable")):
            code, _, err = self.run_cli("--supabase-attributes")
        self.assertEqual(code, 1)
        self.assertIn("Unavailable", err)

    def test_union_rejects_wrong_year_membership_ids_or_completed_courses(self):
        (_, handoff), _ = self.successful("--core-ii-candidates")
        for kind in ("year", "id", "index", "completed"):
            data = deepcopy(handoff)
            block = data["flexible_candidates"]
            if kind == "year": block["catalog_year"] = "2025-2026"
            if kind == "id": block["requirements"][0]["requirement_id"] = "not-a-degree-rule"
            if kind == "index": block["candidate_course_codes"].append("FAKE 999")
            if kind == "completed": data["completed_course_codes_entered"].append("PHL 101")
            with self.assertRaises(ValueError): audit.combined_candidate_codes(data, block)

    def test_projection_is_detached_and_preserves_both_input_objects(self):
        (_, handoff), _ = self.successful("--core-ii-candidates")
        # Rebuild the v1 base using the original audit, then apply already retrieved evidence.
        evidence = audit.fetch_supabase_attribute_evidence(["CS 110"], YEAR)
        base = audit.build_planning_handoff(audit.evaluate_preview(json.loads(DRAFT.read_text()), ["CS 110"], catalog_attribute_evidence=evidence))
        block = handoff["flexible_candidates"]
        results = handoff["course_eligibility"]
        before = deepcopy((base, block, results))
        output = audit.apply_prerequisite_results(base, results, flexible_candidates=block)
        self.assertEqual((base, block, results), before)
        output["flexible_candidates"]["requirements"][0]["candidates"][0]["classification_sources"].clear()
        self.assertEqual((base, block, results), before)


if __name__ == "__main__":
    unittest.main()
