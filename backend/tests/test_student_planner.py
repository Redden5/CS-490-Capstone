import json
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend.main import app
from backend.routes.student_academics import get_student_client

PATH = "/api/v1/student/planner"
PLAN_ID = "ec25d906-67a7-4248-919b-4d5ea098ee09"
REVISION = "88f6fdfb-feb5-493b-8d40-8d04ed99f906"


class StudentPlannerTests(unittest.TestCase):
    def setUp(self):
        self.http = self.enterContext(TestClient(app))

    def use_fake_database(self, handler):
        database = self.enterContext(httpx.Client(
            base_url="https://supabase.invalid", transport=httpx.MockTransport(handler),
        ))
        self.enterContext(patch.dict(app.dependency_overrides, {get_student_client: lambda: database}))

    def test_planner_requires_authentication(self):
        self.assertEqual(self.http.get(PATH).status_code, 401)
        self.assertEqual(self.http.delete(f"{PATH}/semesters/{PLAN_ID}").status_code, 401)

    def test_generated_snapshot_round_trips_even_without_crn(self):
        record = {
            "id": PLAN_ID,
            "term_id": 42,
            "term_name": "Fall 2026",
            "courses": [{
                "course_id": 44, "course_code": "CS 110", "course_title": "",
                "min_credit_hours": 3, "max_credit_hours": 3,
                "selected_section": {"section_id": 2, "section_number": "001", "crn": None},
            }],
            "generated_summary": {
                "status": "TENTATIVE", "warnings": ["Meeting time unavailable"], "unscheduled_required": ["CS 120"],
            },
        }
        saved = {**record, "revision": REVISION}

        def respond(request):
            if request.url.path.endswith("/save_student_semester"):
                payload = json.loads(request.content)
                self.assertEqual(payload["plan_id"], PLAN_ID)
                self.assertIsNone(payload["expected_revision"])
                self.assertEqual(payload["plan_courses"], record["courses"])
                self.assertEqual(payload["summary"], record["generated_summary"])
                return httpx.Response(200, json=REVISION)
            self.assertTrue(request.url.path.endswith("/read_student_planner"))
            return httpx.Response(200, json={"semesters": [saved], "preferences": None})

        self.use_fake_database(respond)
        response = self.http.put(f"{PATH}/semesters", json={**record, "expected_revision": None})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), saved)
        self.assertEqual(self.http.get(PATH).json()["semesters"], [saved])

    def test_preferences_and_delete_handle_empty_rpc_responses(self):
        def respond(request):
            payload = json.loads(request.content)
            if request.url.path.endswith("/save_student_generator_preferences"):
                self.assertEqual(payload, {"selected_term": 42, "credits": 15})
                return httpx.Response(204)
            self.assertTrue(request.url.path.endswith("/delete_student_semester"))
            self.assertEqual(payload["plan_id"], PLAN_ID)
            return httpx.Response(204 if payload["expected_revision"] == REVISION else 409)

        self.use_fake_database(respond)
        response = self.http.put(f"{PATH}/preferences", json={"term_id": 42, "target_credits": 15})
        self.assertEqual(response.status_code, 204)
        for revision, expected in ((REVISION, 204), (PLAN_ID, 409)):
            response = self.http.request("DELETE", f"{PATH}/semesters/{PLAN_ID}", json={"expected_revision": revision})
            self.assertEqual(response.status_code, expected)
