"""HTTP access to the reviewed degree programs and the shared planning service."""

import json
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response

from backend.models.planning import PlanningError, PlanningHandoff, PlanningRequest, SupportedProgram
from backend.services import supabase_service
from backend.services.degree_audit import ReportedCreditError
from backend.services.degree_planning import run_degree_plan


router = APIRouter(prefix="/api/v1/planning", tags=["planning"])
_RESOLVED = Path(__file__).resolve().parents[1] / "scripts/data/degree_requirements/resolved"
PROGRAMS = {
    ("computer-science-bs", "2026-2027"): ("Computer Science, B.S.", _RESOLVED / "computer_science_bs_resolved.json"),
}


def get_client_factory() -> Iterator[Callable[[], Any]]:
    """Defer acquisition to the service and close the HTTP sessions owned by this request."""
    with ExitStack() as sessions:
        def acquire():
            client = supabase_service.create_supabase_client()
            sessions.callback(client.auth.close)
            sessions.enter_context(client.postgrest)
            return client

        yield acquire


@router.get("/programs")
def supported_programs() -> list[SupportedProgram]:
    return [
        SupportedProgram(program_key=key, catalog_year=year, program_title=title)
        for (key, year), (title, _) in PROGRAMS.items()
    ]


@router.post("/evaluate", response_model=PlanningHandoff, responses={
    404: {"model": PlanningError, "description": "Unsupported program and catalog year"},
    422: {"model": PlanningError, "description": "Invalid request or reported credits"},
    500: {"model": PlanningError, "description": "Planning evaluation failed"},
})
def evaluate(
    request: PlanningRequest,
    response: Response,
    client_factory: Annotated[Callable[[], Any], Depends(get_client_factory)],
):
    program = PROGRAMS.get((request.program_key, request.catalog_year))
    if program is None:
        raise HTTPException(404, "Unsupported program and catalog year.")
    title, path = program
    draft = json.loads(path.read_text(encoding="utf-8-sig"))
    if draft["catalog_year"] != request.catalog_year or draft["program_title"] != title:
        raise RuntimeError("Registered degree artifact has inconsistent metadata")

    coursework = request.model_dump(include={"completed_courses"}, exclude_unset=True)["completed_courses"]
    try:
        result = run_degree_plan(
            draft, coursework, reported_credits=request.reported_credits,
            core_ii_candidates=True, client_factory=client_factory,
        )
    except ReportedCreditError:
        raise HTTPException(422, "Reported credits could not be matched to the catalog course and credit range.") from None
    response.headers["Cache-Control"] = "no-store"
    return result.handoff
