"""Shared test helpers."""

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.operationoutcome import OperationOutcome

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def assert_outcome(resp, status: int) -> dict:
    assert resp.status_code == status, resp.text
    body = resp.json()
    OperationOutcome.model_validate(body)
    return body


def create(
    client: TestClient, rtype: str, extra: dict | None = None, rid: str | None = None
) -> str:
    body = {**fixture(rtype), **(extra or {})}
    if rid:
        resp = client.put(f"/fhir/{rtype}/{rid}", json={**body, "id": rid})
        assert resp.status_code in (200, 201), resp.text
        return rid
    resp = client.post(f"/fhir/{rtype}", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def search(client: TestClient, rtype: str, query: str | dict | list) -> dict:
    resp = (
        client.get(f"/fhir/{rtype}", params=query)
        if not isinstance(query, str)
        else client.get(f"/fhir/{rtype}?{query}")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    Bundle.model_validate(body)
    return body


def ids(bundle: dict) -> list[str]:
    return [e["resource"]["id"] for e in bundle.get("entry", [])]


def search_ids(client: TestClient, rtype: str, query: str | dict | list) -> set[str]:
    return set(ids(search(client, rtype, query)))
