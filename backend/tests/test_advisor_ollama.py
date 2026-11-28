"""Local model validation, fallback, and course relationship regressions."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from backend.services.advisor import Record, answer_question, related_courses
from backend.services import advisor_ollama as local


class AdvisorLocalTests(unittest.TestCase):
    def test_reverse_prerequisites_preserve_conditions(self):
        result = answer_question("What courses open up after CS 210?")
        self.assertIn("Additional prerequisites", result["answer"])
        self.assertTrue(result["sources"])
        self.assertTrue(any(record.codes == ("CS 215",) for record in related_courses()["CS 210"]))
        self.assertTrue(any("MTH 229" in record.text for record in related_courses()["CS 210"]))

    def test_alternatives_keep_requirement_selection(self):
        result = answer_question("What are the other electives in the same requirement as CS 315?")
        self.assertIn("Select two", result["answer"])
        self.assertIn("CS 370", result["answer"])

    def test_embedding_cache_reuses_unchanged_records_and_excludes_stale_text(self):
        record = Record("CS 410 · Database Engineering", "Relational databases", "courses", None, ("CS 410",))
        with tempfile.TemporaryDirectory() as folder:
            self.check_cache(Path(folder), record)

    def check_cache(self, folder, record):
        with (
            patch.object(local, "cache_path", return_value=folder / "vectors.sqlite3"),
            patch.object(local, "model_setting", return_value="test-embed"),
            patch.object(local, "embed", return_value=[[1.0, 0.0]]) as embed,
        ):
            self.assertEqual(local.build_embeddings([record]), 1)
            self.assertEqual(local.build_embeddings([record]), 0)
            embed.assert_called_once()
            self.assertEqual(local.semantic_matches("SQL", [record]), [record])
            changed = Record(record.title, "Changed description", "courses", None, record.codes)
            self.assertEqual(local.semantic_matches("SQL", [changed]), [])

    def test_generation_accepts_valid_sources_and_falls_back_on_fabrication(self):
        evidence = {"answer": "CS 410 requires CS 210.", "sources": [{"title": "CS 410", "url": None}],
                    "course_codes": ["CS 410"], "action": None}
        with patch.object(local, "model_setting", return_value="test-chat"), patch.object(local, "ollama") as call:
            call.return_value = {"message": {"content": json.dumps({"answer": "CS 210 is required for CS 410.", "source_ids": [1]})}}
            self.assertIn("is required", local.generate_answer("Prerequisites?", evidence)["answer"])
            for answer in (
                {"answer": "CS 999 is required.", "source_ids": [1]},
                {"answer": "CS 210 is required.", "source_ids": [99]},
            ):
                call.return_value = {"message": {"content": json.dumps(answer)}}
                self.assertEqual(local.generate_answer("Prerequisites?", evidence), evidence)
            call.side_effect = httpx.ConnectError("offline")
            self.assertEqual(local.generate_answer("Prerequisites?", evidence), evidence)

    def test_out_of_scope_and_audit_results_do_not_call_model(self):
        with patch.object(local, "model_setting", return_value="test-chat"), patch.object(local, "ollama") as call:
            for question in ("Tell me a joke", "Can I graduate?"):
                result = answer_question(question)
                self.assertEqual(local.generate_answer(question, result), result)
            call.assert_not_called()
