from pydantic import BaseModel


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