"""Exercise PDF extraction using temporary, synthetic documents."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pymupdf

from backend.utils.pdf_extractor import extract_pdf_text


class PdfExtractorTests(unittest.TestCase):
    def setUp(self):
        temp_dir = self.enterContext(TemporaryDirectory())
        self.path = Path(temp_dir) / "transcript.pdf"

    def test_preserves_page_order_and_sorts_text_within_pages(self):
        with pymupdf.open() as document:
            first = document.new_page()
            first.insert_text((72, 110), "First course")
            first.insert_text((72, 72), "First page heading")
            second = document.new_page()
            second.insert_text((72, 72), "Second course")
            document.save(str(self.path))

        lines = extract_pdf_text(str(self.path)).splitlines()
        self.assertEqual([line.strip() for line in lines if line.strip()], [
            "First page heading",
            "First course",
            "Second course",
        ])

    def test_image_only_pdf_has_no_extractable_text(self):
        with pymupdf.open() as document:
            page = document.new_page()
            image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
            image.clear_with(128)
            page.insert_image(pymupdf.Rect(72, 72, 144, 144), pixmap=image)
            document.save(str(self.path))

        self.assertEqual(extract_pdf_text(str(self.path)).strip(), "")

    def test_missing_pdf_raises_file_not_found(self):
        with self.assertRaises(pymupdf.FileNotFoundError):
            extract_pdf_text(str(self.path))
