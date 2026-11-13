import unittest

from bs4 import BeautifulSoup

from backend.scripts.parse_prerequisites import parse_rows
from backend.scripts.scrape_catalog import parse_subject_page


class CatalogPrerequisiteTests(unittest.TestCase):
    def scrape(self, code, description, fields=""):
        subject = code.split()[0].lower()
        url = f"https://catalog.marshall.edu/undergraduate/courses-az/{subject}/"
        html = (f'<div class="courseblock"><p>{code} Example 3 Credit hours</p>'
                f'<p>{description}</p><ul>{fields}</ul></div>')
        metadata = {}
        course, = parse_subject_page(BeautifulSoup(html, "html.parser"), url, prerequisite_sink=metadata)
        return course, metadata[code]

    def parse(self, course, metadata, reference):
        rows = [dict(field, course_code=course.course_code, catalog_year="2026-2027",
                     source_url=course.source_url) for field in metadata]
        return parse_rows(rows, {course.course_code, reference})["courses"][0]

    def test_separate_prerequisite_and_whitespace_duplicate(self):
        for description in ("Summer training.", "Summer training. (PR: MS   302)"):
            with self.subTest(description=description):
                _, fields = self.scrape("MS 351", description, "<li>Pre-req: MS 302</li>")
                self.assertEqual(fields, [{"field_label": "pre-req", "requirement_text": "MS 302"}])

    def test_ms351_trailing_prerequisite(self):
        description = "Summer training. (PR: MS 302)"
        course, fields = self.scrape("MS 351", description)
        self.assertEqual(course.description, description)
        self.assertEqual(fields, [{"field_label": "pr", "requirement_text": "MS 302"}])
        rule = self.parse(course, fields, "MS 302")["rules"][0]
        self.assertTrue(rule["machine_checkable"])
        self.assertEqual(rule["dnf_clauses"], [[{"course_code": "MS 302", "minimum_grade": None,
                                                "concurrent_allowed": False}]])

    def test_sthm390_preserves_grade_and_gpa(self):
        text = "STHM 290 with minimum B and minimum 2.2 GPA"
        description = f"Sport management experience. (PR: {text})"
        course, fields = self.scrape("STHM 390", description)
        self.assertEqual(course.description, description)
        self.assertEqual(fields, [{"field_label": "pr", "requirement_text": text}])
        parsed = self.parse(course, fields, "STHM 290")
        self.assertEqual(parsed["status"], "NEEDS_REVIEW")
        self.assertEqual(parsed["rules"][0]["dnf_clauses"], [])

    def test_text_after_parenthetical_stays_in_description(self):
        description = "(PR: Permission of instructor). May be repeated for credit."
        course, fields = self.scrape("CMM 270", description)
        self.assertEqual(course.description, description)
        self.assertEqual(fields, [{"field_label": "pr", "requirement_text": "Permission of instructor"}])

    def test_different_sources_require_review(self):
        course, fields = self.scrape("MS 351", "Training (PR: Junior standing)", "<li>Pre-req: MS 302</li>")
        self.assertEqual(fields, [{"field_label": "pre-req", "requirement_text": "MS 302"},
                                  {"field_label": "pr", "requirement_text": "Junior standing"}])
        parsed = self.parse(course, fields, "MS 302")
        self.assertIn("multiple_requirement_fields", parsed["review_flags"])
        self.assertFalse(parsed["complete_enrollment_conditions_machine_checkable"])

    def test_ordinary_empty_nested_and_unbalanced_text(self):
        for description in ("Training (three weeks)", "PR: MS 302", "(PR: )", "(PR: MS 302",
                            "(PR: MS 302))", "(PR: MS 302 (or equivalent))", "(see (PR: MS 302))"):
            with self.subTest(description=description):
                course, fields = self.scrape("MS 351", description)
                self.assertEqual(course.description, description)
                self.assertEqual(fields, [])
