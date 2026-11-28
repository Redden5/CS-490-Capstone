"""Exercise the upload route through PDF extraction and coursework filtering."""

import unittest
from unittest.mock import patch

import pymupdf
from fastapi.testclient import TestClient

from backend.main import app


def transcript_pdf(text=""):
    with pymupdf.open() as document:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
        return document.tobytes()


class TranscriptApiTests(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(TestClient(app))

    def upload(self, pdf, content_type="application/pdf"):
        return self.client.post("/api/v1/transcripts/import", content=pdf,
                                headers={"Content-Type": content_type})

    def test_imports_passing_courses_with_transfer_metadata(self):
        response = self.upload(transcript_pdf(
            "TRANSFER CREDIT ACCEPTED BY INSTITUTION\n"
            "FA24: Example College\n"
            "MAT 121 Calculus I T. 4.000 0.00\n"
            "INSTITUTION CREDIT\n"
            "CSC 141 01 Computer Science I A- 3.000 11.10\n"
            "CSC 142 01 Computer Science II F 3.000 0.00\n"
            "COURSE(S) IN PROGRESS\n"
            "CSC 240 01 Data Structures 3.000"
        ))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json(), {
            "courses": [
                {"course_code": "MAT 121", "grade": "T", "credits": "4.000", "is_transfer": True},
                {"course_code": "CSC 141", "grade": "A-", "credits": "3.000", "is_transfer": False},
            ],
            "skipped_courses": 2,
            "review_courses": [],
        })

    def test_transfer_placeholders_are_returned_for_review(self):
        response = self.upload(transcript_pdf(
            "TRANSFER CREDIT ACCEPTED BY INSTITUTION\n"
            "FA24: Example College\n"
            "PSY 201 Psychology B. 4.000 12.00\n"
            "UNC 1XX Unclassified CR. 1.000 0.00\n"
            "UNC 1XX Unclassified D. 4.000 4.00\n"
        ))
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual([course["course_code"] for course in data["courses"]], ["PSY 201"])
        self.assertEqual([course["credits"] for course in data["review_courses"]], ["1.000", "4.000"])
        self.assertEqual(data["skipped_courses"], 0)

    def test_invalid_and_unrecognized_pdfs_return_validation_errors(self):
        for pdf in (b"%PDF-invalid", transcript_pdf(), transcript_pdf("Unrecognized text")):
            with self.subTest(pdf_size=len(pdf)):
                self.assertEqual(self.upload(pdf).status_code, 422)

    def test_rejects_wrong_content_type(self):
        self.assertEqual(self.upload(b"not a PDF", "text/plain").status_code, 415)

    def test_rejects_oversize_document(self):
        with patch("backend.routes.transcripts.MAX_PDF_BYTES", 5):
            self.assertEqual(self.upload(b"%PDF-oversize").status_code, 413)
