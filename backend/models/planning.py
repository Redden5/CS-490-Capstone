"""HTTP inputs and the top-level v2.1 handoff contract."""

from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.services.degree_audit import canonical_course_code
from backend.services.degree_planning import normalize_reported_credits
from backend.services.prerequisite_evaluator import _code as prerequisite_course_code


# The enriched workflow accepts the intersection of audit and prerequisite identities.
CourseCode = Annotated[str, AfterValidator(canonical_course_code), AfterValidator(prerequisite_course_code)]


class CompletedCourse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    course_code: CourseCode
    grade: str | None = None
    equivalency_verified: bool = True


class PlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    program_key: str = Field(min_length=1)
    catalog_year: str = Field(pattern=r"^[0-9]{4}-[0-9]{4}$")
    completed_courses: list[CourseCode | CompletedCourse]
    reported_credits: dict[str, float | str] = Field(default_factory=dict)

    @field_validator("reported_credits")
    @classmethod
    def normalize_credits(cls, credits):
        return normalize_reported_credits(credits.items())

    @model_validator(mode="after")
    def check_reported_courses(self) -> Self:
        completed = {c if isinstance(c, str) else c.course_code for c in self.completed_courses}
        if self.reported_credits.keys() - completed:
            raise ValueError("Reported credits must refer to completed courses")
        return self


class SupportedProgram(BaseModel):
    program_key: str
    program_title: str
    catalog_year: str


class PlanningError(BaseModel):
    detail: str
    fields: list[str] | None = None


class PlanningHandoff(BaseModel):
    """Keep service-owned evidence, provenance, and additional fields intact."""

    model_config = ConfigDict(extra="allow", strict=True)

    schema_version: Literal["2.1.0"]
    purpose: str
    degree: dict[str, Any]
    completed_course_codes_entered: list[str]
    distinct_course_credit_evidence: dict[str, Any] | None
    requirements: list[dict[str, Any]]
    summary: dict[str, Any]
    scheduler_must_still_check: list[str]
    warnings: list[str]
    course_eligibility: dict[str, Any]
    candidate_option_coverage: dict[str, Any]
    flexible_candidates: dict[str, Any]
