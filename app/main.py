"""App factory, lifespan, logging middleware, /health (SPEC 4.5)."""

import json
import logging
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import Settings, get_settings
from app.db import init_db, make_engine
from app.errors import issue, outcome, register_handlers
from app.routes import router

log = logging.getLogger("leanfhir")


class JsonFormatter(logging.Formatter):
    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": f"{self.formatTime(record, '%Y-%m-%dT%H:%M:%S')}.{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log.handlers[:] = [handler]
    log.setLevel(level.upper())
    log.propagate = False


def _resource_fields(path: str) -> dict[str, str]:
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "fhir" and parts[1] != "metadata":
        fields = {"resource_type": parts[1]}
        if len(parts) >= 3 and not parts[2].startswith("_"):
            fields["resource_id"] = parts[2]
        return fields
    return {}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        init_db(engine)
        app.state.engine = engine
        yield
        engine.dispose()

    app = FastAPI(title="LeanFHIR", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    register_handlers(app)

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["ETag", "Location", "Last-Modified"],
        )

    @app.middleware("http")
    async def access_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            fields = {
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                **_resource_fields(request.url.path),
            }
            log.info("request", extra={"fields": fields})

    @app.get("/health", response_model=None)
    def health(request: Request) -> JSONResponse:
        try:
            with request.app.state.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            log.exception("health check failed")
            return outcome(503, [issue("exception", "Database unavailable")])
        return JSONResponse({"status": "ok"})

    app.include_router(router)
    return app


app = create_app()
