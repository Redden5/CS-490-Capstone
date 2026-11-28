import json
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend.main import app
from backend.routes.planning import evaluate as evaluate_planning
from backend.routes.student_academics import get_student_client


PATH = "/api/v1/student/academics"
REVISION = "7d69b5bb-2f68-4eee-9789-be2b82a8b883"
ATTEMPTS = [
    {
        "id": "4de5d899-e4b8-42e6-ae86-70d9875955d0",
        "course_code": "CS 110",
        "grade": "B",
        "reported_credits": 3.0,
        "equivalency_verified": True,
    },
    {
        "id": "54c2881b-078a-4bff-828e-4736bd833261",
        "course_code": "CS 110",
        "grade": None,
        "reported_credits": None,
        "equivalency_verified": False,
    },
]
INPUT = {"program_key": "computer-science-bs", "catalog_year": "2026-2027", "attempts": ATTEMPTS}
RECORD = {**INPUT, "revision": REVISION}


class StudentAcademicsTests(unittest.TestCase):
    def setUp(self):
        self.http = self.enterContext(TestClient(app, raise_server_exceptions=False))

    def use_fake_database(self, handler):
        database = self.enterContext(httpx.Client(
            base_url="https://supabase.invalid", transport=httpx.MockTransport(handler),
        ))
        self.enterContext(patch.dict(app.dependency_overrides, {get_student_client: lambda: database}))

    def test_private_routes_require_authentication(self):
        self.assertEqual(self.http.get(PATH).status_code, 401)
        self.assertEqual(self.http.put(PATH, json={**INPUT, "expected_revision": None}).status_code, 401)
        self.assertEqual(self.http.post(f"{PATH}/evaluate", json={"revision": REVISION}).status_code, 401)

    def test_reads_saved_attempts_in_order_without_caching(self):
        def reply(request):
            self.assertEqual(request.url.path, "/rest/v1/rpc/read_student_academics")
            return httpx.Response(200, json=RECORD)

        self.use_fake_database(reply)
        response = self.http.get(PATH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), RECORD)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_save_preserves_attempts_and_conflict_status(self):
        def reply(request):
            self.assertEqual(request.url.path, "/rest/v1/rpc/save_student_academics")
            payload = json.loads(request.content)
            self.assertEqual(payload["attempts"], ATTEMPTS)
            self.assertEqual(payload["selected_program"], INPUT["program_key"])
            return httpx.Response(409) if payload["expected_revision"] else httpx.Response(200, json=REVISION)

        self.use_fake_database(reply)
        response = self.http.put(PATH, json={**INPUT, "expected_revision": None})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), RECORD)
        response = self.http.put(PATH, json={**INPUT, "expected_revision": REVISION})
        self.assertEqual(response.status_code, 409)

    def test_save_rejects_conflicting_credits_and_duplicate_attempt_ids(self):
        def unexpected_request(_):
            self.fail("Invalid coursework reached the database")

        self.use_fake_database(unexpected_request)
        with self.subTest("conflicting credits on aliases"):
            attempts = [ATTEMPTS[0], {**ATTEMPTS[1], "course_code": "cs110", "reported_credits": 4}]
            response = self.http.put(PATH, json={**INPUT, "attempts": attempts, "expected_revision": None})
            self.assertEqual(response.status_code, 422)
        with self.subTest("duplicate attempt IDs"):
            response = self.http.put(PATH, json={**INPUT, "attempts": [ATTEMPTS[0]] * 2, "expected_revision": None})
            self.assertEqual(response.status_code, 422)

    def test_evaluate_uses_saved_attempts_and_rejects_stale_revision(self):
        self.use_fake_database(lambda _: httpx.Response(200, json=RECORD))
        # Let the route call the existing evaluator, then inspect the request it received.
        with patch("backend.routes.student_academics.evaluate", wraps=evaluate_planning) as evaluate:
            response = self.http.post(f"{PATH}/evaluate", json={"revision": "00000000-0000-0000-0000-000000000000"})
            self.assertEqual(response.status_code, 409)
            evaluate.assert_not_called()

            with patch("backend.routes.planning.run_degree_plan") as run_plan:
                run_plan.side_effect = RuntimeError("Stop before evaluating catalog evidence")
                self.http.post(f"{PATH}/evaluate", json={"revision": REVISION})
            saved_request = evaluate.call_args.args[0]
            self.assertEqual(saved_request.reported_credits, {"CS 110": 3})
            self.assertEqual([course.grade for course in saved_request.completed_courses], ["B", None])
            self.assertFalse(saved_request.completed_courses[1].equivalency_verified)
