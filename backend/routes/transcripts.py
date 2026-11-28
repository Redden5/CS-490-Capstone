"""Import completed coursework from a transcript PDF."""

from decimal import Decimal

import pymupdf
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool

from backend.models.models import TranscriptCourse
from backend.models.planning import CompletedCourse
from backend.services.transcript_service import parse_transcript
from backend.utils.pdf_extractor import extract_pdf_text

router = APIRouter(prefix="/api/v1/transcripts", tags=["transcripts"])
MAX_PDF_BYTES = 5 * 1024 * 1024
COURSES = TypeAdapter(list[TranscriptCourse])
PASSING_GRADES = {"A", "B", "C", "D", "P", "S", "CR"}
TRANSFER_GRADES = PASSING_GRADES | {"T", "TR"}


class ImportedCourse(BaseModel):
    course_code: str
    grade: str
    credits: Decimal | None
    is_transfer: bool


class ReviewCourse(ImportedCourse):
    course_title: str


class TranscriptImport(BaseModel):
    courses: list[ImportedCourse]
    skipped_courses: int
    review_courses: list[ReviewCourse] = Field(default_factory=list)


def read_coursework(pdf: bytes) -> TranscriptImport:
    try:
        text = extract_pdf_text(pdf)
    except (pymupdf.FileDataError, ValueError) as error:
        raise HTTPException(422, "Choose a readable, unlocked PDF.") from error
    if not text.strip():
        raise HTTPException(422, "This PDF has no selectable text. Use a text-based transcript PDF.")

    parsed = COURSES.validate_python(parse_transcript(text))
    if not parsed:
        raise HTTPException(422, "No course rows were recognized. Check the transcript format or enter courses manually.")

    courses = []
    review_courses = []
    for course in parsed:
        is_transfer = course.level is None
        allowed_grades = TRANSFER_GRADES if is_transfer else PASSING_GRADES
        if course.grade is None or course.grade.rstrip("+-") not in allowed_grades:
            continue
        imported = ImportedCourse(
            course_code=f"{course.subject_code} {course.course_number}",
            grade=course.grade,
            credits=course.credits if course.credits > 0 else None,
            is_transfer=is_transfer,
        )
        try:
            CompletedCourse(course_code=imported.course_code, equivalency_verified=not is_transfer)
        except ValidationError:
            review_courses.append(ReviewCourse(**imported.model_dump(), course_title=course.course_title))
            continue
        courses.append(imported)
    return TranscriptImport(courses=courses, review_courses=review_courses,
                            skipped_courses=len(parsed) - len(courses) - len(review_courses))


@router.post("/import", response_model=TranscriptImport)
async def import_transcript(request: Request, response: Response) -> TranscriptImport:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/pdf":
        raise HTTPException(415, "Choose a PDF file.")
    pdf = bytearray()
    async for chunk in request.stream():
        if len(pdf) + len(chunk) > MAX_PDF_BYTES:
            raise HTTPException(413, "Choose a PDF no larger than 5 MB.")
        pdf.extend(chunk)
    if not pdf.startswith(b"%PDF-"):
        raise HTTPException(422, "Choose a readable, unlocked PDF.")

    response.headers["Cache-Control"] = "no-store"
    return await run_in_threadpool(read_coursework, bytes(pdf))
