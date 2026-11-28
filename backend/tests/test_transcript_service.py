"""Exercise transcript parsing with synthetic text."""

import unittest

from backend.services.transcript_service import parse_transcript


class TranscriptServiceTests(unittest.TestCase):
    def test_parses_coursework_across_sections(self):
        transcript = """TRANSFER CREDIT ACCEPTED BY INSTITUTION
FA24: Example College
MAT 121 Calculus I T. 4.000 0.00
INSTITUTION CREDIT
Term: Spring 2025
CSC 141 01 Computer Science I A 3.000 12.00
COURSE(S) IN PROGRESS
Term: Fall 2025
CSC 142 01 Computer Science II 3.000
TRANSCRIPT TOTALS
CSC 240 01 Data Structures A 3.000 12.00
"""
        self.assertEqual(parse_transcript(transcript), [
            {
                "subject_code": "MAT", "course_number": "121",
                "course_title": "Calculus I", "grade": "T",
                "credits": "4.000", "quality_points": "0.00",
                "term": "Fall 2024", "institution": "Example College",
            },
            {
                "subject_code": "CSC", "course_number": "141", "level": "01",
                "course_title": "Computer Science I", "grade": "A",
                "credits": "3.000", "quality_points": "12.00",
                "term": "Spring 2025", "institution": None,
            },
            {
                "subject_code": "CSC", "course_number": "142", "level": "01",
                "course_title": "Computer Science II", "credits": "3.000",
                "term": "Fall 2025", "institution": None,
            },
        ])

    def test_recovers_attached_grade_and_wrapped_title(self):
        transcript = (
            "INSTITUTION CREDIT\n"
            "CSC 141 01 Introduction toA 3.000 12.00\n"
            "  Computer Science\n"
            "  Term Totals\n"
        )
        course, = parse_transcript(transcript)
        self.assertEqual(course["course_title"], "Introduction to Computer Science")
        self.assertEqual(course["grade"], "A")

    def test_empty_text_yields_no_courses(self):
        self.assertEqual(parse_transcript(""), [])

    def test_preserves_plus_and_minus_grades(self):
        transcript = (
            "INSTITUTION CREDIT\n"
            "CSC 141 01 Computer Science I A- 3.000 11.10\n"
            "CSC 142 01 Computer Science II B+ 3.000 9.90\n"
        )
        self.assertEqual([course["grade"] for course in parse_transcript(transcript)], ["A-", "B+"])

    def test_accepts_two_decimal_places_for_in_progress_credits(self):
        transcript = "COURSE(S) IN PROGRESS\nCSC 142 01 Computer Science II 3.00\n"
        self.assertEqual([course["credits"] for course in parse_transcript(transcript)], ["3.00"])

    def test_indented_course_row_does_not_extend_previous_title(self):
        transcript = (
            "INSTITUTION CREDIT\n"
            "  CSC 141 01 Computer Science I A 3.000 12.00\n"
            "  CSC 142 01 Computer Science II B 3.000 9.00\n"
        )
        titles = [course["course_title"] for course in parse_transcript(transcript)]
        self.assertEqual(titles, ["Computer Science I", "Computer Science II"])
