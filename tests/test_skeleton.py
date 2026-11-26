"""M1 acceptance: health, CapabilityStatement, OperationOutcome errors, DB setup, logging."""

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fhir.resources.R4B.capabilitystatement import CapabilityStatement
from fhir.resources.R4B.operationoutcome import OperationOutcome
from sqlalchemy import create_engine, inspect, text

from app.config import Settings
from app.db import is_sqlite
from app.main import JsonFormatter, create_app
from app.registry import REGISTRY

SIX_TYPES = {"Patient", "Practitioner", "PractitionerRole", "Organization", "Location", "Encounter"}


def assert_outcome(resp, status: int) -> dict:
    assert resp.status_code == status
    assert resp.headers["content-type"].startswith("application/fhir+json")
    body = resp.json()
    OperationOutcome.model_validate(body)
    assert body["issue"]
    return body


def test_health_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_db_failure_returns_503(client: TestClient, app: FastAPI) -> None:
    good = app.state.engine
    app.state.engine = create_engine("sqlite:////nonexistent-dir/x/y.db")
    try:
        assert_outcome(client.get("/health"), 503)
    finally:
        app.state.engine = good


def test_metadata_is_valid_capability_statement(client: TestClient) -> None:
    resp = client.get("/fhir/metadata")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/fhir+json; charset=utf-8"
    body = resp.json()
    CapabilityStatement.model_validate(body)
    assert body["fhirVersion"] == "4.0.1"
    assert body["format"] == ["json"]
    resources = body["rest"][0]["resource"]
    assert {r["type"] for r in resources} == SIX_TYPES


def test_metadata_generated_from_registry(client: TestClient) -> None:
    resources = {r["type"]: r for r in client.get("/fhir/metadata").json()["rest"][0]["resource"]}
    for rtype, rdef in REGISTRY.items():
        names = {p["name"] for p in resources[rtype]["searchParam"]}
        assert names == {"_id", "_lastUpdated"} | {p.name for p in rdef.params}


def test_metadata_uses_configured_base_url(tmp_path) -> None:
    s = Settings(
        database_url=f"sqlite:///{tmp_path / 'b.db'}", base_url="https://fhir.example/fhir/"
    )
    with TestClient(create_app(s)) as c:
        assert (
            c.get("/fhir/metadata").json()["implementation"]["url"] == "https://fhir.example/fhir"
        )


def test_unsupported_resource_type_404(client: TestClient) -> None:
    body = assert_outcome(client.get("/fhir/Observation/1"), 404)
    assert body["issue"][0]["code"] == "not-found"


def test_unsupported_method_405(client: TestClient) -> None:
    body = assert_outcome(client.post("/fhir/metadata"), 405)
    assert body["issue"][0]["code"] == "not-supported"


def test_unhandled_error_500_without_stack_trace(app: FastAPI) -> None:
    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internals")

    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/boom")
    body = assert_outcome(resp, 500)
    assert body["issue"][0]["code"] == "exception"
    assert "secret" not in resp.text and "Traceback" not in resp.text


def test_tables_created(client: TestClient, app: FastAPI) -> None:
    tables = set(inspect(app.state.engine).get_table_names())
    assert {"resource", "search_index"} <= tables


def test_sqlite_pragmas(client: TestClient, app: FastAPI) -> None:
    engine = app.state.engine
    if not is_sqlite(str(engine.url)):
        pytest.skip("SQLite only")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000
        assert conn.execute(text("PRAGMA synchronous")).scalar() == 1  # NORMAL


def test_access_log_is_structured(client: TestClient) -> None:
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger = logging.getLogger("leanfhir")
    logger.addHandler(handler)
    try:
        client.get("/fhir/Patient/abc")
    finally:
        logger.removeHandler(handler)
    entry = json.loads(JsonFormatter().format(records[-1]))
    assert entry["method"] == "GET"
    assert entry["path"] == "/fhir/Patient/abc"
    assert entry["status"] == 404
    assert entry["resource_type"] == "Patient"
    assert entry["resource_id"] == "abc"
    assert isinstance(entry["duration_ms"], float)


def test_cors_off_by_default(client: TestClient) -> None:
    resp = client.get("/health", headers={"Origin": "https://app.example"})
    assert "access-control-allow-origin" not in resp.headers


def test_cors_when_configured(tmp_path) -> None:
    s = Settings(database_url=f"sqlite:///{tmp_path / 'c.db'}", cors_origins="https://app.example")
    with TestClient(create_app(s)) as c:
        resp = c.get("/health", headers={"Origin": "https://app.example"})
    assert resp.headers["access-control-allow-origin"] == "https://app.example"


def test_fhir_error_and_validation_error_handlers(app: FastAPI) -> None:
    from fhir.resources.R4B.patient import Patient

    from app.errors import FhirError

    @app.get("/raise-fhir")
    def raise_fhir() -> None:
        raise FhirError(409, "conflict", "in use")

    @app.get("/raise-validation")
    def raise_validation() -> None:
        Patient.model_validate({"resourceType": "Patient", "name": [{"given": [5]}], "foo": 1})

    with TestClient(app) as c:
        body = assert_outcome(c.get("/raise-fhir"), 409)
        assert body["issue"][0] == {
            "severity": "error",
            "code": "conflict",
            "diagnostics": "in use",
        }
        body = assert_outcome(c.get("/raise-validation"), 422)
        exprs = {e for i in body["issue"] for e in i.get("expression", [])}
        assert {"name[0].given[0]", "foo"} <= exprs
