from pydantic import BaseModel


class Course(BaseModel):
    course_code: str
    subject_code: str
    course_number: str
    course_title: str
    description: str | None = None
    credit_hours: str | None = None
    active_status: bool = True