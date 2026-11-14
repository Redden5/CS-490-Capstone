"""ASGI entry point for the local degree planning API."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.models.planning import PlanningRequest
from backend.routes.planning import router
from backend.routes.schedules import router as schedules_router


app = FastAPI(title="Degree Planning API", version="1")


@app.middleware("http")
async def sanitized_server_errors(request: Request, call_next):
    # Return failures inside CORS so browsers can read the sanitized response.
    try:
        return await call_next(request)
    except Exception:
        return JSONResponse(status_code=500, content={"detail": "Unable to complete the planning request."})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(router)
app.include_router(schedules_router)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, error: RequestValidationError):
    # Report the affected request field without echoing coursework, arbitrary keys or raw input.
    locations = (e["loc"] for e in error.errors())
    fields = sorted({loc[1] for loc in locations if len(loc) > 1 and loc[1] in PlanningRequest.model_fields})
    return JSONResponse(status_code=422, content={"detail": "Invalid planning request.", "fields": fields})
