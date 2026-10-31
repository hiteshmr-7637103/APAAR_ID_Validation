"""Uniform error shape (spec section 8):

{ "error": {"code": "...", "message_en": "...", "message_key": "...", "details": {...}} }
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message_en: str,
        message_key: str | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message_en = message_en
        self.message_key = message_key or f"errors.{code.lower()}"
        self.details = details or {}
        self.headers = headers


def error_body(code: str, message_en: str, message_key: str, details: dict[str, Any] | None = None):
    return {
        "error": {
            "code": code,
            "message_en": message_en,
            "message_key": message_key,
            "details": details or {},
        }
    }


_STATUS_CODES = {
    400: ("BAD_REQUEST", "Bad request."),
    401: ("AUTH_REQUIRED", "Authentication required."),
    403: ("FORBIDDEN", "You do not have permission to perform this action."),
    404: ("NOT_FOUND", "Not found."),
    405: ("METHOD_NOT_ALLOWED", "Method not allowed."),
    429: ("RATE_LIMITED", "Too many requests."),
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message_en, exc.message_key, exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        code, default_msg = _STATUS_CODES.get(exc.status_code, ("ERROR", "Request failed."))
        message = exc.detail if isinstance(exc.detail, str) else default_msg
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, message, f"errors.{code.lower()}"),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        errors = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body(
                "VALIDATION_ERROR",
                "Some fields are missing or invalid.",
                "errors.validation_error",
                {"errors": errors},
            ),
        )
