"""Authenticated coursework persistence and evaluation of saved records."""

from collections.abc import Callable, Iterator
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.models.planning import CompletedCourse, PlanningHandoff, PlanningRequest
from backend.routes.planning import PROGRAMS, evaluate, get_client_factory
from backend.services.supabase_service import student_supabase_credentials

router = APIRouter(prefix="/api/v1/student/academics", tags=["student academics"])
bearer = HTTPBearer(auto_error=False)


class CourseworkAttempt(CompletedCourse):
    id: UUID = Field(strict=False)
    reported_credits: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @field_validator("grade")
    @classmethod
    def validate_grade(cls, value: str | None) -> str | None:
        grade = (value or "").strip().upper()
        if grade == "F" or len(grade) > 20:
            raise ValueError("Enter a passing grade or leave the grade blank")
        return grade or None


class AcademicInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    program_key: str
    catalog_year: str
    attempts: list[CourseworkAttempt]

    def planning_request(self) -> PlanningRequest:
        credits: dict[str, float] = {}
        for attempt in self.attempts:
            value = attempt.reported_credits
            if value is not None:
                if credits.setdefault(attempt.course_code, value) != value:
                    raise HTTPException(422, "Use consistent reported credits for repeated courses.")
        return PlanningRequest(
            program_key=self.program_key,
            catalog_year=self.catalog_year,
            completed_courses=[
                CompletedCourse(**attempt.model_dump(include={"course_code", "grade", "equivalency_verified"}))
                for attempt in self.attempts
            ],
            reported_credits=credits,
        )


class AcademicRecord(AcademicInput):
    revision: UUID


class SaveAcademics(AcademicInput):
    expected_revision: UUID | None


class EvaluateAcademics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: UUID


def get_student_client(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Iterator[httpx.Client]:
    if credentials is None:
        raise HTTPException(401, "Sign in to access your coursework.", headers={"WWW-Authenticate": "Bearer"})

    url, key = student_supabase_credentials()
    with httpx.Client(base_url=url.rstrip("/"), headers={
        "apikey": key, "Authorization": f"Bearer {credentials.credentials}",
    }) as client:
        try:
            identity = client.get("/auth/v1/user")
        except httpx.RequestError:
            raise HTTPException(503, "Unable to verify your session.") from None
        if identity.status_code in {401, 403}:
            raise HTTPException(401, "Sign in again to access your coursework.")
        if not identity.is_success:
            raise HTTPException(503, "Unable to verify your session.")
        yield client


StudentClient = Annotated[httpx.Client, Depends(get_student_client)]


def student_rpc(client: httpx.Client, name: str, arguments: dict, *, resource: str = "coursework") -> Any:
    try:
        result = client.post(f"/rest/v1/rpc/{name}", json=arguments)
        if result.status_code == 409:
            raise HTTPException(409, f"Saved {resource} changed elsewhere. Reload before saving again.")
        if result.status_code in {401, 403}:
            raise HTTPException(401, "Sign in again to access your coursework.")
        result.raise_for_status()
        return result.json() if result.content else None
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, f"Saved {resource} is temporarily unavailable.") from None


@router.get("", response_model=AcademicRecord | None)
def read_academics(response: Response, client: StudentClient):
    response.headers["Cache-Control"] = "no-store"
    return student_rpc(client, "read_student_academics", {})


@router.put("", response_model=AcademicRecord)
def save_academics(request: SaveAcademics, response: Response, client: StudentClient):
    if (request.program_key, request.catalog_year) not in PROGRAMS:
        raise HTTPException(404, "Unsupported program and catalog year.")
    if len({attempt.id for attempt in request.attempts}) != len(request.attempts):
        raise HTTPException(422, "Coursework attempt IDs must be unique.")
    request.planning_request()
    record = request.model_dump(mode="json", exclude={"expected_revision"})
    revision = student_rpc(client, "save_student_academics", {
        "expected_revision": str(request.expected_revision) if request.expected_revision else None,
        "selected_program": request.program_key,
        "selected_year": request.catalog_year,
        "attempts": record["attempts"],
    })
    response.headers["Cache-Control"] = "no-store"
    return {**record, "revision": revision}


@router.post("/evaluate", response_model=PlanningHandoff)
def evaluate_academics(
    request: EvaluateAcademics,
    response: Response,
    client: StudentClient,
    client_factory: Annotated[Callable[[], Any], Depends(get_client_factory)],
):
    saved = student_rpc(client, "read_student_academics", {})
    if saved is None or saved["revision"] != str(request.revision):
        raise HTTPException(409, "Coursework changed elsewhere. Reload it before evaluating again.")
    record = AcademicRecord.model_validate(saved)
    if (record.program_key, record.catalog_year) not in PROGRAMS:
        raise HTTPException(404, "Unsupported program and catalog year.")
    return evaluate(record.planning_request(), response, client_factory)
