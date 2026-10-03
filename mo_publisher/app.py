"""Public pages and anonymous report admission, served separately from MO Hub."""

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from mo_publisher.pages import load_settings, pages, stylesheet
from mo_publisher.reports import MAX_BODY_BYTES, ReportError, configured_store, validate_report


def create_app() -> FastAPI:
    """Explicit configuration/home prevent accidental use of an operator's Hub."""
    settings = load_settings(Path(os.environ["MO_PUBLISHER_CONFIG"]))
    store = configured_store()
    store.purge()
    rendered = pages(settings)
    css = stylesheet()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def safe_responses(request: Request, call_next):
        try:
            response = await call_next(request)
        except (sqlite3.Error, OSError):
            response = JSONResponse({"error": "Reporting is temporarily unavailable"}, status_code=503)
        response.headers.update({
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'; style-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        })
        return response

    @app.get("/style.css")
    def style():
        return Response(css, media_type="text/css")

    @app.get("/health")
    def health():
        # Bounded real DB read, not a success response over a failed store.
        with store.connect() as db:
            db.execute("SELECT report_id FROM reports LIMIT 1").fetchone()
        return {"status": "ok"}

    @app.post("/api/publisher/ai-reports")
    async def report(request: Request):
        # Production binds loopback; the reverse proxy owns per-IP rate limiting.
        # No Hub authorization can be accepted or persisted by this independent API.
        if "authorization" in request.headers or "cookie" in request.headers:
            return JSONResponse({"error": "Do not send credentials with a report"}, status_code=400)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return JSONResponse({"error": "JSON required"}, status_code=415)
        if request.headers.get("content-encoding", "identity") != "identity":
            return JSONResponse({"error": "Compressed reports are not supported"}, status_code=415)
        try:
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_BODY_BYTES:
                    raise ReportError(413, "Report is too large")
                raw.extend(chunk)
            data = validate_report(bytes(raw))
            receipt, created = await run_in_threadpool(store.accept, data)
        except ReportError as exc:
            headers = {"Retry-After": "3600"} if exc.status == 503 else {}
            return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=headers)
        return JSONResponse({"status": "received", "receipt": receipt}, status_code=201 if created else 200,
                            headers={"X-MO-Report-Receipt": receipt})

    @app.get("/{path:path}", response_class=HTMLResponse)
    def public_page(path: str):
        body = rendered.get("/" + path)
        if body is None:
            return HTMLResponse("<!doctype html><html lang=en><title>Page not found</title><p>Page not found. <a href='/'>MO Everywhere</a></p></html>", status_code=404)
        return HTMLResponse(body)

    return app
