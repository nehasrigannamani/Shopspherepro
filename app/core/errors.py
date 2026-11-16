"""Consistent error responses.

Every error keeps the legacy ``detail`` key (existing clients and tests rely on
it) and additionally carries an ``error`` envelope::

    {"detail": "...", "error": {"code": "NOT_FOUND", "message": "...",
                                "request_id": "...", "fields": [...]}}
"""
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("shopsphere.errors")

CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
}


def _request_id(request: Request) -> str | None:
    return request.headers.get("x-request-id")


def error_body(request: Request, status_code: int, detail, fields=None) -> dict:
    message = detail if isinstance(detail, str) else "Request failed"
    envelope = {
        "code": CODES.get(status_code, "ERROR" if status_code < 500 else "INTERNAL_ERROR"),
        "message": message,
        "request_id": _request_id(request),
    }
    if fields:
        envelope["fields"] = fields
    return {"detail": detail, "error": envelope}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(request, exc.status_code, exc.detail),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Never echo submitted values back: they may contain passwords/tokens.
        fields = [
            {"field": ".".join(str(p) for p in e.get("loc", ())), "message": e.get("msg", "")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body(
                request, 422, [{"loc": list(e.get("loc", ())), "msg": e.get("msg", ""),
                                "type": e.get("type", "")} for e in exc.errors()],
                fields,
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_body(request, 500, "Internal server error"),
        )
