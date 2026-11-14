"""HTTP contract tests against the real planning service and SELECT-only fixtures."""

from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch

from fastapi.testclient import TestClient

from backend.main import app
from backend.routes import planning
from backend.services import flexible_candidates, prerequisite_loader, supabase_service
from backend.services.degree_planning import run_degree_plan
from backend.tests.test_core_ii_planning import Client, DRAFT, fixture


BASE = "/api/v1/planning"
PROGRAM = {"program_key": "computer-science-bs", "catalog_year": "2026-2027"}


class PlanningApiTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        tables, attributes, catalog, prerequisites = fixture(include_ct=True)
        self.db = Client(tables)
        self.factory = Mock(return_value=self.db)
        stack.enter_context(patch.dict(app.dependency_overrides, {planning.get_client_factory: lambda: self.factory}))
        stack.enter_context(patch.object(supabase_service, "create_supabase_client", side_effect=AssertionError("live client")))
        files = {"catalog_attributes.csv": attributes, "courses.csv": catalog, "catalog_prerequisites.csv": prerequisites}
        stack.enter_context(patch.object(Path, "read_bytes", autospec=True, side_effect=lambda p: files[p.name]))
        stack.enter_context(patch.object(flexible_candidates, "_SOURCE_SHA256", hashlib.sha256(attributes).hexdigest()))
        stack.enter_context(patch.multiple(prerequisite_loader, _CATALOG_SHA256=hashlib.sha256(catalog).hexdigest(),
                                          _PREREQUISITE_SHA256=hashlib.sha256(prerequisites).hexdigest()))
        self.http = stack.enter_context(TestClient(app, raise_server_exceptions=False))

    def post(self, **fields):
        return self.http.post(BASE + "/evaluate", json={**PROGRAM, "completed_courses": [], **fields})

    def test_complete_handoff_matches_direct_service_and_uses_one_client(self):
        records = ["art210", {"course_code": "cs110", "grade": " a "}, {"course_code": "MTH 229", "grade": None}]
        credits = {"art210": "1"}
        response = self.post(completed_courses=records, reported_credits=credits)
        self.assertEqual(response.status_code, 200, response.text)
        actual = response.json()
        expected = run_degree_plan(json.loads(DRAFT.read_text(encoding="utf-8")), records, reported_credits=credits,
                                   core_ii_candidates=True, client=self.db).handoff
        self.assertEqual(actual, expected)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.factory.assert_called_once_with()
        self.assertEqual(actual["schema_version"], "2.1.0")
        memberships = actual["flexible_candidates"]["course_requirement_memberships"]
        self.assertEqual(memberships["HST 101"], ["t0_row_0012", "t0_row_0013"])
        courses = actual["course_eligibility"]["courses"]
        self.assertEqual(len(courses), len({c["course_code"] for c in courses}))
        self.assertTrue(any(c["status"] == "UNKNOWN" and not c["loading"]["data_complete"] for c in courses))

    def test_explicit_false_matches_default_v21(self):
        default = self.post()
        explicit = self.post(critical_thinking_candidates=False)
        self.assertEqual(default.status_code, 200)
        self.assertEqual(explicit.status_code, 200)
        self.assertEqual(explicit.json(), default.json())
        handoff = default.json()
        self.assertEqual(handoff["schema_version"], "2.1.0")
        self.assertNotIn("t0_row_0005", [r["requirement_id"] for r in handoff["flexible_candidates"]["requirements"]])

    def test_ct_opt_in_returns_service_v22_with_shared_eligibility(self):
        records = ["MTH 229", "CS 110"]
        response = self.post(completed_courses=records, critical_thinking_candidates=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        handoff = response.json()
        expected = run_degree_plan(json.loads(DRAFT.read_text(encoding="utf-8")), records,
                                   critical_thinking_candidates=True, client=self.db).handoff
        self.assertEqual(handoff, expected)
        self.assertEqual(handoff["schema_version"], "2.2.0")
        rules = {r["rule_id"]: r for r in handoff["requirements"]}
        for rule_id in ("t0_row_0004", "t0_row_0022"):
            self.assertEqual(rules[rule_id]["candidate_progress_status"], "CANDIDATE_MET")
        flexible = handoff["flexible_candidates"]
        ct = next(r for r in flexible["requirements"] if r["requirement_id"] == "t0_row_0005")
        self.assertEqual(ct["remaining_credits_candidate"], 3)
        self.assertNotIn("MTH 229", [c["course_code"] for c in ct["candidates"]])
        self.assertEqual(flexible["course_requirement_memberships"]["ANT 201"], ["t0_row_0005", "t0_row_0013"])
        ant = next(c for c in ct["candidates"] if c["course_code"] == "ANT 201")
        matches = [c for c in handoff["course_eligibility"]["courses"] if c["course_code"] == ant["course_eligibility_reference"]]
        self.assertEqual([c["status"] for c in matches], ["MET"])
        self.assertEqual(ant["prerequisite_status"], "MET")

    def test_optional_grades_repeats_and_equivalency_reach_evaluator(self):
        graded = {"course_code": "CS 110", "grade": "B"}
        for records, expected in (
            ([graded], "MET"), (["CS 110"], "UNKNOWN"), ([{"course_code": "CS 110", "grade": None}], "UNKNOWN"),
            ([graded, "cs110"], "UNKNOWN"), ([{**graded, "equivalency_verified": False}], "UNKNOWN"),
        ):
            with self.subTest(records=records):
                response = self.post(completed_courses=records)
                self.assertEqual(response.status_code, 200)
                course = next(c for c in response.json()["course_eligibility"]["courses"] if c["course_code"] == "CS 210")
                self.assertEqual(course["status"], expected)

    def test_program_registry_and_unsupported_combinations(self):
        response = self.http.get(BASE + "/programs")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{**PROGRAM, "program_title": "Computer Science, B.S."}])
        for fields in ({"catalog_year": "2025-2026"}, {"program_key": "other"}, {"program_key": "../private.json"}):
            self.assertEqual(self.post(**fields).status_code, 404)
        self.factory.assert_not_called()

    def test_malformed_requests_are_rejected_before_client_creation(self):
        for fields in (
            *({"critical_thinking_candidates": value} for value in ("true", 1, None)),
            {"completed_courses": None}, {"completed_courses": ["CS 1"]}, {"completed_courses": [42]},
            {"completed_courses": [{"course_code": "CS 110", "grade": 4}]},
            {"completed_courses": [{"course_code": "CS 110", "equivalency_verified": "false"}]},
            {"completed_courses": [{"course_code": "CS 110", "unsupported": True}]},
            {"draft_path": "private-path"}, {"query": "private-query"},
            *({"completed_courses": ["cs110"], "reported_credits": {"cs110": v}}
              for v in (0, -1, True, "NaN", "inf", "invalid", None)),
            {"completed_courses": ["cs110"], "reported_credits": {"cs110": 3, "CS 110": 2}},
            {"reported_credits": {"CS 110": 3}},
        ):
            with self.subTest(fields=fields):
                response = self.post(**fields)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("private-", response.text)
        self.factory.assert_not_called()

    def test_out_of_range_reported_credit_is_a_client_error(self):
        response = self.post(completed_courses=["CS 110"], reported_credits={"CS 110": 9})
        self.assertEqual(response.status_code, 422)
        self.assertIn("credit range", response.json()["detail"])

    def test_default_dependency_closes_owned_sessions_on_success_and_credit_error(self):
        for credits, status in ((3, 200), (9, 422)):
            with self.subTest(credits=credits), patch.dict(app.dependency_overrides, clear=True):
                self.db.auth = Mock()
                self.db.postgrest = MagicMock()
                with patch.object(supabase_service, "create_supabase_client", return_value=self.db) as create:
                    response = self.post(completed_courses=["CS 110"], reported_credits={"CS 110": credits})
                self.assertEqual(response.status_code, status, response.text)
                create.assert_called_once_with()
                self.db.auth.close.assert_called_once_with()
                self.db.postgrest.__exit__.assert_called_once()

    def test_unavailable_configuration_returns_a_degraded_handoff(self):
        self.factory.side_effect = ValueError("private credentials")
        response = self.post()
        self.assertEqual(response.status_code, 200)
        handoff = response.json()
        self.assertEqual(handoff["flexible_candidates"]["generation_status"], "UNAVAILABLE")
        eligibility = handoff["course_eligibility"]
        self.assertEqual(eligibility["evaluation_status"], "UNAVAILABLE")
        self.assertEqual({c["status"] for c in eligibility["courses"]}, {"UNKNOWN"})
        self.assertNotIn("private credentials", response.text)
        self.factory.assert_called_once_with()

    def test_query_failure_preserves_candidates_and_diagnostics(self):
        def fail(query, response):
            if query.table_name == "courserule":
                raise RuntimeError("private SDK error")
            return response

        self.db.hook = fail
        response = self.post(completed_courses=["CS 110"])
        self.assertEqual(response.status_code, 200)
        handoff = response.json()
        self.assertTrue(handoff["flexible_candidates"]["candidate_course_codes"])
        self.assertEqual({c["status"] for c in handoff["course_eligibility"]["courses"]}, {"UNKNOWN"})
        self.assertEqual(handoff["course_eligibility"]["issues"], [{"code": "QUERY_ERROR", "table": "courserule"}])
        self.assertNotIn("private SDK error", response.text)

    def test_internal_errors_are_sanitized(self):
        with patch.object(planning, "run_degree_plan", side_effect=RuntimeError("private server path")):
            response = self.post()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to complete the planning request."})

    def test_development_cors(self):
        for origin, allowed in (("http://localhost:5173", True), ("https://other.invalid", False)):
            response = self.http.options(BASE + "/evaluate", headers={
                "Origin": origin, "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            })
            self.assertEqual(response.headers.get("access-control-allow-origin"), origin if allowed else None)

    def test_cold_application_import_does_not_initialize_supabase(self):
        # Fresh interpreter so already-imported router modules cannot mask eager initialization.
        subprocess.run([sys.executable, "-B", "-c", """
from unittest.mock import patch
with patch('backend.services.supabase_service.create_supabase_client', side_effect=AssertionError) as factory:
    from backend.main import app
    assert '/api/v1/planning/evaluate' in app.openapi()['paths']
    factory.assert_not_called()
"""], cwd=DRAFT.parents[5], check=True)
