from pydantic import BaseModel
from decimal import Decimal

class Course(BaseModel):
    course_code: str
    subject_code: str
    course_number: str
    course_title: str
    description: str | None = None
    min_credit_hours: float
    max_credit_hours: float
    active_status: bool = True
    source_url: str | None = None


class TranscriptCourse(BaseModel):
    subject_code: str
    course_number: str
    course_title: str
    credits: Decimal
    level: str | None = None
    grade: str | None = None
    quality_points: Decimal | None = None
    term: str | None
    institution: str | None = None