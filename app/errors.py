"""FhirError and OperationOutcome construction (SPEC 5.6)."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

FHIR_JSON = "application/fhir+json; charset=utf-8"


class FhirResponse(JSONResponse):
    media_type = FHIR_JSON


class FhirError(Exception):
    def __init__(self, status: int, issue_code: str, diagnostics: str) -> None:
        super().__init__(diagnostics)
        self.status = status
        self.issue_code = issue_code
        self.diagnostics = diagnostics


def issue(code: str, diagnostics: str, expression: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"severity": "error", "code": code, "diagnostics": diagnostics}
    if expression:
        out["expression"] = [expression]
    return out


def outcome(status: int, issues: list[dict[str, Any]]) -> FhirResponse:
    return FhirResponse({"resourceType": "OperationOutcome", "issue": issues}, status_code=status)


def _loc_to_expression(loc: tuple[int | str, ...]) -> str:
    expr = ""
    for part in loc:
        expr += f"[{part}]" if isinstance(part, int) else (f".{part}" if expr else str(part))
    return expr


def validation_issues(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        issue("structure", err["msg"], _loc_to_expression(tuple(err["loc"])) or None)
        for err in exc.errors()
    ]


_HTTP_ISSUE_CODES = {404: "not-found", 405: "not-supported", 406: "not-supported"}


def register_handlers(app: FastAPI) -> None:
    @app.exception_handler(FhirError)
    def _fhir_error(_: Request, exc: FhirError) -> FhirResponse:
        return outcome(exc.status, [issue(exc.issue_code, exc.diagnostics)])

    @app.exception_handler(ValidationError)
    def _validation_error(_: Request, exc: ValidationError) -> FhirResponse:
        return outcome(422, validation_issues(exc))

    @app.exception_handler(RequestValidationError)
    def _request_validation_error(_: Request, exc: RequestValidationError) -> FhirResponse:
        msgs = "; ".join(str(e.get("msg")) for e in exc.errors())
        return outcome(400, [issue("invalid", msgs or "Invalid request")])

    @app.exception_handler(StarletteHTTPException)
    def _http_error(request: Request, exc: StarletteHTTPException) -> FhirResponse:
        code = _HTTP_ISSUE_CODES.get(exc.status_code, "processing")
        detail = str(exc.detail)
        if exc.status_code == 404 and detail == "Not Found":
            detail = f"Unknown resource type or path: {request.url.path}"
        resp = outcome(exc.status_code, [issue(code, detail)])
        if exc.headers:
            resp.headers.update(exc.headers)
        return resp

    @app.exception_handler(Exception)
    def _unhandled(request: Request, exc: Exception) -> FhirResponse:
        logging.getLogger("leanfhir").error(
            "unhandled error", exc_info=exc, extra={"fields": {"path": request.url.path}}
        )
        return outcome(500, [issue("exception", "Internal server error")])
