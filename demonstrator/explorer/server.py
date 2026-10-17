"""Loopback-only API and static frontend. Run with one worker."""

import logging
import secrets
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import APP_ROOT
from .live import LiveService
from .replay import BundledReplayStore
from interaction.openai_structured import OpenAIStructuredError


class ForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: int
    method: Literal["reactive_clarification"]
    step_id: int = Field(ge=0)
    mode: Literal["original", "changed"]


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    request_id: str = Field(min_length=8, max_length=80)
    action: Literal["reply", "ask", "repair", "suggest", "stop", "generate", "feedback", "continue"]
    text: str = Field(default="", max_length=4000)


class CustomRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    db_id: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=4000)


def create_app(store=None, live=None):
    store = store or BundledReplayStore()
    live = live or LiveService(store)
    token = secrets.token_urlsafe(32)
    app = FastAPI(title="SQL interaction explorer", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse(
                    {"detail": "Cross-origin requests are not allowed."}, status_code=403
                )
            if not secrets.compare_digest(request.headers.get("x-explorer-token", ""), token):
                return JSONResponse(
                    {"detail": "Refresh the page before starting a live request."}, status_code=403
                )
            if int(request.headers.get("content-length", "0") or "0") > 32_000:
                return JSONResponse({"detail": "Request is too large."}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc).strip("'")}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(OpenAIStructuredError)
    async def provider_error(request, exc):
        # Never return provider exception strings, which can include request details.
        logging.warning("Live API failure: %s", type(exc).__name__)
        return JSONResponse(
            {
                "detail": "The API call failed or the demo spend limit was reached. No automatic retry was made. Your branch is unchanged; you can retry explicitly."
            },
            status_code=502,
        )

    @app.get("/api/status")
    def status():
        return {"token": token, "live": live.status()}

    @app.get("/api/catalogue")
    def catalogue():
        return store.catalogue()

    @app.get("/api/databases")
    def databases():
        return live.databases.catalogue()

    @app.get("/api/databases/{db_id}")
    def database(db_id: str):
        return live.databases.detail(db_id)

    @app.post("/api/custom")
    def custom(body: CustomRequest):
        return live.start_custom(body.db_id, body.question)

    @app.get("/api/cases/{case_id}")
    def case(case_id: int):
        return store.case(case_id)

    @app.get("/api/cases/{case_id}/{method}/{step_id}/context")
    def context(case_id: int, method: str, step_id: int):
        return store.context(case_id, method, step_id)

    @app.get("/api/cases/{case_id}/{method}/{step_id}/preview")
    def preview(case_id: int, method: str, step_id: int):
        return store.preview(case_id, method, step_id)

    @app.post("/api/branches")
    def fork(body: ForkRequest):
        return live.fork(body.case_id, body.method, body.step_id, body.mode)

    @app.get("/api/branches/{sid}")
    def branch(sid: str):
        return live.get(sid)

    @app.post("/api/branches/{sid}/actions")
    def action(sid: str, body: ActionRequest):
        return live.action(sid, body.revision, body.request_id, body.action, body.text)

    dist = APP_ROOT / "frontend/dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
