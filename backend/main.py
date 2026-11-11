"""ASGI entry point for the local degree planning API."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.models.planning import PlanningRequest
from backend.routes.planning import router


app = FastAPI(title="Degree Planning API", version="1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(router)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, error: RequestValidationError):
    # Report the affected request field without echoing coursework, arbitrary keys or raw input.
    locations = (e["loc"] for e in error.errors())
    fields = sorted({loc[1] for loc in locations if len(loc) > 1 and loc[1] in PlanningRequest.model_fields})
    return JSONResponse(status_code=422, content={"detail": "Invalid planning request.", "fields": fields})


@app.exception_handler(Exception)
async def internal_error(request: Request, error: Exception):
    return JSONResponse(status_code=500, content={"detail": "Unable to complete the planning request."})
