import pymupdf

def extract_pdf_text(file_path: str) -> str:
    with pymupdf.open(file_path) as document:
        content = ""
        for page in document:
            content += page.get_text("text", sort=True) + "\n"

    return content


