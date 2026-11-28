from threading import Lock

import pymupdf

_PDF_LOCK = Lock()


def extract_pdf_text(file_path: str | bytes) -> str:
    """Extract embedded text from a PDF path or an uploaded PDF."""
    # Serialize PyMuPDF calls for concurrent uploads within this process.
    with _PDF_LOCK:
        document = (
            pymupdf.open(stream=file_path, filetype="pdf")
            if isinstance(file_path, bytes)
            else pymupdf.open(file_path)
        )
        with document:
            if not document.is_pdf or document.needs_pass:
                raise ValueError("Expected an unlocked PDF")
            return "".join(page.get_text("text", sort=True) + "\n" for page in document)


