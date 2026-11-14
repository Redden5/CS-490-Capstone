"""Read-only draft schedule endpoints for the existing degree-planning UI.

These endpoints do not register students, store coursework or clear prerequisites.
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.models.planning import CourseCode, PlanningHandoff
from backend.routes.planning import get_client_factory
from backend.services.schedule_engine import (
    course_credit_values,
    generate_from_handoff,
    get_course_ids,
    group_sections_by_course,
    read_explicit_plan,
    replace_section,
    replace_flexible_course,
)

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])
ClientFactory = Annotated[Callable[[], Any], Depends(get_client_factory)]


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    handoff: PlanningHandoff
    term_id: int = Field(gt=0)
    target_credits: float = Field(default=15.0, gt=0, le=21)
    max_results: int = Field(default=5, ge=1, le=5)


class SectionOptionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    course_code: CourseCode
    term_id: int = Field(gt=0)


class SwapCourseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    handoff: PlanningHandoff
    term_id: int = Field(gt=0)
    current_sections: dict[CourseCode, int] = Field(min_length=1, max_length=21)
    old_course_code: CourseCode
    new_course_code: CourseCode
    new_section_id: int = Field(gt=0)


class SwapSectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    handoff: PlanningHandoff
    term_id: int = Field(gt=0)
    current_sections: dict[CourseCode, int] = Field(min_length=1, max_length=21)
    course_code: CourseCode
    new_section_id: int = Field(gt=0)


def _ensure_active_term(client: Any, term_id: int) -> None:
    """Only use terms currently enabled in the university term catalog.

    Per-student administrator term grants are not yet represented by this API.
    """
    response = (client.table("term").select("term_id,active_status")
                .eq("term_id", term_id).limit(1).execute())
    if not response.data or response.data[0].get("active_status") is not True:
        raise HTTPException(422, "The requested academic term is not active.")


def _supported_codes(plan: dict) -> set[str]:
    codes = set(plan["required_courses"])
    for group in plan["choice_groups"]:
        codes.update(group["course_codes"])
    return codes


def _handoff_plan(handoff: PlanningHandoff) -> dict:
    try:
        return read_explicit_plan(handoff.model_dump())
    except ValueError:
        raise HTTPException(422, "Unsupported planning handoff.") from None


@router.post("/generate")
def generate(request: GenerateRequest, response: Response, client_factory: ClientFactory):
    _handoff_plan(request.handoff)
    client = client_factory()
    _ensure_active_term(client, request.term_id)
    try:
        result = generate_from_handoff(
            request.handoff.model_dump(), request.term_id, client=client,
            target_credits=request.target_credits, max_results=request.max_results,
            max_credits=21, administrator_approved=False,
        )
    except ValueError:
        raise HTTPException(422, "Unable to generate schedules from the provided planning data.") from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/sections")
def sections(request: SectionOptionsRequest, response: Response, client_factory: ClientFactory):
    client = client_factory()
    _ensure_active_term(client, request.term_id)
    grouped = group_sections_by_course({request.course_code}, request.term_id, client=client)
    response.headers["Cache-Control"] = "no-store"
    return {"course_code": request.course_code, "sections": grouped[request.course_code]}


@router.post("/swap-section")
def swap_section(request: SwapSectionRequest, response: Response, client_factory: ClientFactory):
    plan = _handoff_plan(request.handoff)
    current_codes = set(request.current_sections)
    allowed = _supported_codes(plan)
    if (request.course_code not in current_codes or not current_codes <= allowed
            or len(current_codes) > 21):
        raise HTTPException(422, "Selected courses do not match the degree-planning candidates.")

    client = client_factory()
    _ensure_active_term(client, request.term_id)
    catalog = get_course_ids(current_codes, client=client)
    credits, _ = course_credit_values(catalog)
    grouped = group_sections_by_course(current_codes, request.term_id, client=client, catalog_courses=catalog)
    selected = {}
    for code, selected_id in request.current_sections.items():
        existing = next((s for s in grouped.get(code, []) if s["section_id"] == selected_id), None)
        if existing is None:
            raise HTTPException(422, "One or more selected sections are no longer listed for this term.")
        selected[code] = existing

    result = replace_section(
        selected, request.course_code, request.new_section_id,
        grouped, credits, eligibility_statuses=plan["eligibility_statuses"],
        max_credits=21, administrator_approved=False,
    )
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/swap-course")
def swap_course(request: SwapCourseRequest, response: Response, client_factory: ClientFactory):
    plan = _handoff_plan(request.handoff)
    current_codes = set(request.current_sections)
    allowed = _supported_codes(plan)
    if (request.old_course_code not in current_codes or not current_codes <= allowed
            or request.new_course_code not in allowed or len(current_codes) > 21):
        raise HTTPException(422, "Selected courses do not match the degree-planning candidates.")

    client = client_factory()
    _ensure_active_term(client, request.term_id)
    requested_codes = current_codes | {request.new_course_code}
    catalog = get_course_ids(requested_codes, client=client)
    credits, _ = course_credit_values(catalog)
    grouped = group_sections_by_course(requested_codes, request.term_id, client=client, catalog_courses=catalog)
    selected = {}
    for code, selected_id in request.current_sections.items():
        existing = next((s for s in grouped.get(code, []) if s["section_id"] == selected_id), None)
        if existing is None:
            raise HTTPException(422, "One or more selected sections are no longer listed for this term.")
        selected[code] = existing

    result = replace_flexible_course(
        selected, request.old_course_code, request.new_course_code,
        request.new_section_id, grouped, credits,
        set(plan["required_courses"]), plan["choice_groups"],
        eligibility_statuses=plan["eligibility_statuses"],
        max_credits=21, administrator_approved=False,
    )
    response.headers["Cache-Control"] = "no-store"
    return result
