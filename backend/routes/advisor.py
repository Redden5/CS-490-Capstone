"""Read-only chat endpoint for Marshall academic questions."""

from collections import defaultdict, deque
import math
from threading import Lock
from time import monotonic
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from backend.services.advisor import answer_question
from backend.services.advisor_ollama import generate_answer


router = APIRouter(prefix="/api/v1/advisor", tags=["advisor"])
WINDOW_SECONDS = 60
REQUESTS_PER_CLIENT = 10
REQUESTS_PER_PROCESS = 120


class AdvisorQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    course_codes: list[Annotated[str, StringConstraints(max_length=20)]] = Field(default_factory=list, max_length=3)


class AdvisorSource(BaseModel):
    title: str
    url: str | None


class AdvisorAnswer(BaseModel):
    answer: str
    sources: list[AdvisorSource]
    course_codes: list[str]
    action: Literal["planner", "audit"] | None


class AdvisorRateLimit:
    def __init__(self):
        self.requests = deque()
        self.clients = defaultdict(deque)
        self.lock = Lock()

    def retry_after(self, client):
        with self.lock:
            now = monotonic()
            cutoff = now - WINDOW_SECONDS
            while self.requests and self.requests[0][0] <= cutoff:
                _, expired_client = self.requests.popleft()
                client_requests = self.clients[expired_client]
                client_requests.popleft()
                if not client_requests:
                    del self.clients[expired_client]

            recent = self.clients.get(client, ())
            if len(recent) >= REQUESTS_PER_CLIENT:
                return math.ceil(recent[0] + WINDOW_SECONDS - now)
            if len(self.requests) >= REQUESTS_PER_PROCESS:
                return math.ceil(self.requests[0][0] + WINDOW_SECONDS - now)
            self.clients[client].append(now)
            self.requests.append((now, client))
            return 0


rate_limit = AdvisorRateLimit()


@router.post("/chat", response_model=AdvisorAnswer)
def chat(payload: AdvisorQuestion, request: Request, response: Response):
    client = request.client.host if request.client else "unknown"
    retry_after = rate_limit.retry_after(client)
    if retry_after:
        raise HTTPException(429, "Too many Advisor questions.", headers={
            "Retry-After": str(retry_after), "Cache-Control": "no-store",
        })
    response.headers["Cache-Control"] = "no-store"
    try:
        result = answer_question(payload.question, payload.course_codes)
    except OSError:
        raise HTTPException(503, "Advisor academic data is unavailable.") from None
    return generate_answer(payload.question, result)
