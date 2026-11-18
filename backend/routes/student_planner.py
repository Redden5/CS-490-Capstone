"""Authenticated persistence for semester plans and generator settings."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.routes.student_academics import StudentClient, student_rpc

router = APIRouter(prefix="/api/v1/student/planner", tags=["student planner"])
PositiveID = Annotated[int, Field(gt=0, le=9007199254740991)]
Credit = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class PlannerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SectionSnapshot(PlannerModel):
    section_id: PositiveID
    section_number: str | int | None
    crn: str | int | None


class PlannedCourse(PlannerModel):
    course_id: PositiveID
    course_code: str = Field(min_length=1, max_length=40)
    course_title: str
    min_credit_hours: Credit
    max_credit_hours: Credit
    selected_section: SectionSnapshot | None = None

    @model_validator(mode="after")
    def validate_range(self):
        if self.min_credit_hours > self.max_credit_hours:
            raise ValueError("Minimum credits must not exceed maximum credits.")
        return self


class GeneratedSummary(PlannerModel):
    status: Literal["TENTATIVE", "CHECKS_PASSED_NOT_REGISTRATION_CLEARANCE"]
    warnings: list[str]
    unscheduled_required: list[str]


class Semester(PlannerModel):
    id: UUID
    term_id: PositiveID | None
    term_name: str | None
    courses: list[PlannedCourse] = Field(max_length=100)
    generated_summary: GeneratedSummary | None

    @model_validator(mode="after")
    def validate_courses(self):
        if len({course.course_id for course in self.courses}) != len(self.courses):
            raise ValueError("A course can only appear once per semester.")
        return self


class SavedSemester(Semester):
    revision: UUID


class SaveSemester(Semester):
    expected_revision: UUID | None


class DeleteSemester(PlannerModel):
    expected_revision: UUID


class Preferences(PlannerModel):
    term_id: PositiveID | None
    target_credits: float = Field(ge=1, le=21, allow_inf_nan=False)


class PlannerRecord(PlannerModel):
    semesters: list[SavedSemester]
    preferences: Preferences | None


@router.get("", response_model=PlannerRecord)
def read_planner(response: Response, client: StudentClient):
    response.headers["Cache-Control"] = "no-store"
    return student_rpc(client, "read_student_planner", {}, resource="planner")


@router.put("/semesters", response_model=SavedSemester)
def save_semester(request: SaveSemester, response: Response, client: StudentClient):
    record = request.model_dump(mode="json", exclude={"expected_revision"})
    revision = student_rpc(client, "save_student_semester", {
        "plan_id": record["id"],
        "expected_revision": str(request.expected_revision) if request.expected_revision else None,
        "selected_term": record["term_id"],
        "term_label": record["term_name"],
        "summary": record["generated_summary"],
        "plan_courses": record["courses"],
    }, resource="planner")
    response.headers["Cache-Control"] = "no-store"
    return {**record, "revision": revision}


@router.delete("/semesters/{plan_id}", status_code=204)
def delete_semester(plan_id: UUID, request: DeleteSemester, client: StudentClient):
    student_rpc(client, "delete_student_semester", {
        "plan_id": str(plan_id), "expected_revision": str(request.expected_revision),
    }, resource="planner")
    return Response(status_code=204)


@router.put("/preferences", status_code=204)
def save_preferences(request: Preferences, client: StudentClient):
    student_rpc(client, "save_student_generator_preferences", {
        "selected_term": request.term_id, "credits": request.target_credits,
    }, resource="planner")
    return Response(status_code=204)
