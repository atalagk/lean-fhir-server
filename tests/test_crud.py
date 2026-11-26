"""M2 acceptance: CRUD, validation, versioning, format negotiation (per type)."""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fhir.resources.R4B.operationoutcome import OperationOutcome

from app.registry import REGISTRY

FIXTURES = Path(__file__).parent / "fixtures"
TYPES = list(REGISTRY)


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def assert_outcome(resp, status: int) -> dict:
    assert resp.status_code == status, resp.text
    body = resp.json()
    OperationOutcome.model_validate(body)
    return body


def create(client: TestClient, rtype: str, body: dict | None = None) -> dict:
    resp = client.post(f"/fhir/{rtype}", json=body or fixture(rtype))
    assert resp.status_code == 201, resp.text
    return resp.json()


def without_meta(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in ("id", "meta")}


@pytest.mark.parametrize("rtype", TYPES)
def test_create(client: TestClient, rtype: str) -> None:
    body = {**fixture(rtype), "id": "client-chosen", "meta": {"versionId": "99"}}
    resp = client.post(f"/fhir/{rtype}", json=body)
    assert resp.status_code == 201
    out = resp.json()
    assert out["id"] != "client-chosen"
    assert uuid.UUID(out["id"]).version == 4
    assert resp.headers["location"] == f"http://testserver/fhir/{rtype}/{out['id']}"
    assert resp.headers["etag"] == 'W/"1"'
    assert resp.headers["content-type"] == "application/fhir+json; charset=utf-8"
    assert out["meta"]["versionId"] == "1"
    assert len(out["meta"]["lastUpdated"]) == 24 and out["meta"]["lastUpdated"].endswith("Z")
    assert without_meta(out) == without_meta(body)


@pytest.mark.parametrize("rtype", TYPES)
def test_read(client: TestClient, rtype: str) -> None:
    created = create(client, rtype)
    resp = client.get(f"/fhir/{rtype}/{created['id']}")
    assert resp.status_code == 200
    assert resp.json() == created
    assert resp.headers["etag"] == 'W/"1"'
    assert resp.headers["last-modified"].endswith("GMT")


@pytest.mark.parametrize("rtype", TYPES)
def test_read_unknown_id_404(client: TestClient, rtype: str) -> None:
    body = assert_outcome(client.get(f"/fhir/{rtype}/does-not-exist"), 404)
    assert body["issue"][0]["code"] == "not-found"


@pytest.mark.parametrize("rtype", TYPES)
def test_update_existing(client: TestClient, rtype: str) -> None:
    created = create(client, rtype)
    body = {**created, "language": "en-NZ"}
    resp = client.put(f"/fhir/{rtype}/{created['id']}", json=body)
    assert resp.status_code == 200
    assert resp.headers["etag"] == 'W/"2"'
    out = resp.json()
    assert out["meta"]["versionId"] == "2"
    assert out["meta"]["lastUpdated"] >= created["meta"]["lastUpdated"]
    assert client.get(f"/fhir/{rtype}/{created['id']}").json() == out


@pytest.mark.parametrize("rtype", TYPES)
def test_update_as_create(client: TestClient, rtype: str) -> None:
    resp = client.put(f"/fhir/{rtype}/my-id.1", json={**fixture(rtype), "id": "my-id.1"})
    assert resp.status_code == 201
    assert resp.headers["etag"] == 'W/"1"'
    assert resp.headers["location"].endswith(f"/fhir/{rtype}/my-id.1")
    assert resp.json()["id"] == "my-id.1"


@pytest.mark.parametrize("rtype", TYPES)
def test_update_id_mismatch_400(client: TestClient, rtype: str) -> None:
    assert_outcome(client.put(f"/fhir/{rtype}/a", json={**fixture(rtype), "id": "b"}), 400)
    assert_outcome(client.put(f"/fhir/{rtype}/a", json=fixture(rtype)), 400)  # id missing


@pytest.mark.parametrize("rtype", TYPES)
def test_update_bad_id_format_400(client: TestClient, rtype: str) -> None:
    for bad in ("has_underscore", "x" * 65):
        assert_outcome(client.put(f"/fhir/{rtype}/{bad}", json={**fixture(rtype), "id": bad}), 400)


@pytest.mark.parametrize("rtype", TYPES)
def test_if_match(client: TestClient, rtype: str) -> None:
    created = create(client, rtype)
    url = f"/fhir/{rtype}/{created['id']}"
    client.put(url, json=created)  # -> version 2
    stale = client.put(url, json=created, headers={"If-Match": 'W/"1"'})
    assert assert_outcome(stale, 412)["issue"][0]["code"] == "conflict"
    current = client.put(url, json=created, headers={"If-Match": 'W/"2"'})
    assert current.status_code == 200
    assert current.headers["etag"] == 'W/"3"'


def test_if_match_malformed_400(client: TestClient) -> None:
    created = create(client, "Patient")
    resp = client.put(f"/fhir/Patient/{created['id']}", json=created, headers={"If-Match": "abc"})
    assert_outcome(resp, 400)


def test_if_match_on_absent_resource_412(client: TestClient) -> None:
    body = {**fixture("Patient"), "id": "new1"}
    assert_outcome(client.put("/fhir/Patient/new1", json=body, headers={"If-Match": 'W/"1"'}), 412)


@pytest.mark.parametrize("rtype", TYPES)
def test_invalid_structure_422(client: TestClient, rtype: str) -> None:
    body = {**fixture(rtype), "language": 5, "bogusElement": 1}
    out = assert_outcome(client.post(f"/fhir/{rtype}", json=body), 422)
    exprs = {e for i in out["issue"] for e in i["expression"]}
    assert {"language", "bogusElement"} <= exprs
    assert all(i["code"] == "structure" for i in out["issue"])


def test_patient_gender_int_422(client: TestClient) -> None:
    out = assert_outcome(
        client.post("/fhir/Patient", json={**fixture("Patient"), "gender": 5}), 422
    )
    assert out["issue"][0]["expression"] == ["gender"]


def test_nested_error_location_422(client: TestClient) -> None:
    body = {**fixture("Patient"), "name": [{"given": ["ok", 7]}]}
    out = assert_outcome(client.post("/fhir/Patient", json=body), 422)
    assert out["issue"][0]["expression"] == ["name[0].given[1]"]


def test_missing_required_element_422(client: TestClient) -> None:
    body = {k: v for k, v in fixture("Encounter").items() if k != "class"}
    assert_outcome(client.post("/fhir/Encounter", json=body), 422)


def test_invalid_on_update_422(client: TestClient) -> None:
    body = {**fixture("Patient"), "id": "p1", "gender": 5}
    assert_outcome(client.put("/fhir/Patient/p1", json=body), 422)
    assert_outcome(client.get("/fhir/Patient/p1"), 404)


def test_meta_not_object_422(client: TestClient) -> None:
    assert_outcome(client.post("/fhir/Patient", json={**fixture("Patient"), "meta": 1}), 422)


@pytest.mark.parametrize("rtype", TYPES)
def test_wrong_resource_type_400(client: TestClient, rtype: str) -> None:
    other = "Organization" if rtype != "Organization" else "Patient"
    assert_outcome(client.post(f"/fhir/{rtype}", json=fixture(other)), 400)
    body = {**fixture(other), "id": "x1"}
    assert_outcome(client.put(f"/fhir/{rtype}/x1", json=body), 400)


@pytest.mark.parametrize("rtype", TYPES)
def test_malformed_json_400(client: TestClient, rtype: str) -> None:
    headers = {"Content-Type": "application/fhir+json"}
    assert_outcome(
        client.post(f"/fhir/{rtype}", content=b'{"resourceType": ', headers=headers), 400
    )
    assert_outcome(client.post(f"/fhir/{rtype}", content=b"[1, 2]", headers=headers), 400)
    assert_outcome(client.put(f"/fhir/{rtype}/x", content=b"not json", headers=headers), 400)


def test_content_types(client: TestClient) -> None:
    raw = json.dumps(fixture("Patient")).encode()
    for ctype in (
        "application/fhir+json",
        "application/json",
        "application/fhir+json; charset=utf-8",
    ):
        resp = client.post("/fhir/Patient", content=raw, headers={"Content-Type": ctype})
        assert resp.status_code == 201
    xml = client.post(
        "/fhir/Patient", content=b"<Patient/>", headers={"Content-Type": "application/fhir+xml"}
    )
    assert_outcome(xml, 415)


@pytest.mark.parametrize("rtype", TYPES)
def test_delete(client: TestClient, rtype: str) -> None:
    created = create(client, rtype)
    url = f"/fhir/{rtype}/{created['id']}"
    resp = client.delete(url)
    assert resp.status_code == 204 and resp.content == b""
    assert_outcome(client.get(url), 404)
    assert client.delete(url).status_code == 204


def test_delete_then_put_restarts_version(client: TestClient) -> None:
    body = {**fixture("Patient"), "id": "p2"}
    client.put("/fhir/Patient/p2", json=body)
    client.delete("/fhir/Patient/p2")
    resp = client.put("/fhir/Patient/p2", json=body)
    assert resp.status_code == 201 and resp.headers["etag"] == 'W/"1"'


def test_extensions_round_trip(client: TestClient) -> None:
    rich = fixture("Patient-rich")
    created = create(client, "Patient", rich)
    stored = client.get(f"/fhir/Patient/{created['id']}").json()
    assert without_meta(stored) == without_meta(rich)
    assert stored["_birthDate"] == rich["_birthDate"]
    assert stored["name"][0]["_given"] == rich["name"][0]["_given"]
    # profile/tag kept; versionId/lastUpdated server-set
    assert stored["meta"]["profile"] == rich["meta"]["profile"]
    assert stored["meta"]["tag"] == rich["meta"]["tag"]
    updated = client.put(f"/fhir/Patient/{created['id']}", json=stored).json()
    assert without_meta(updated) == without_meta(rich)


@pytest.mark.parametrize(
    ("params", "headers"),
    [
        ({"_format": "xml"}, {}),
        ({"_format": "application/fhir+xml"}, {}),
        ({}, {"Accept": "application/fhir+xml"}),
        ({}, {"Accept": "application/xml, text/xml"}),
    ],
)
def test_xml_requested_406(client: TestClient, params: dict, headers: dict) -> None:
    created = create(client, "Patient")
    for url in (f"/fhir/Patient/{created['id']}", "/fhir/metadata"):
        out = assert_outcome(client.get(url, params=params, headers=headers), 406)
        assert out["issue"][0]["code"] == "not-supported"
    assert_outcome(
        client.post("/fhir/Patient", json=fixture("Patient"), params=params, headers=headers), 406
    )


@pytest.mark.parametrize(
    ("params", "headers"),
    [
        ({"_format": "json"}, {}),
        ({"_format": "application/fhir+json"}, {}),
        ({}, {"Accept": "application/fhir+json"}),
        ({}, {"Accept": "application/fhir+xml;q=1, application/json;q=0.5"}),
        ({}, {"Accept": "text/html, */*;q=0.8"}),
    ],
)
def test_json_accepted(client: TestClient, params: dict, headers: dict) -> None:
    created = create(client, "Patient")
    assert (
        client.get(f"/fhir/Patient/{created['id']}", params=params, headers=headers).status_code
        == 200
    )


def test_unsupported_type_and_method(client: TestClient) -> None:
    assert_outcome(client.post("/fhir/Observation", json={"resourceType": "Observation"}), 404)
    assert_outcome(client.put("/fhir/Observation/1", json={"resourceType": "Observation"}), 404)
    assert_outcome(client.delete("/fhir/Observation/1"), 404)
    assert_outcome(client.patch("/fhir/Patient/1", json={}), 405)
    assert_outcome(client.post("/fhir/Patient/1", json={}), 405)


def test_concurrent_update_conflict_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import store

    created = create(client, "Patient")
    url = f"/fhir/Patient/{created['id']}"
    client.put(url, json=created)  # DB now at version 2
    real_load = store._load
    # Simulate a writer that read version 1 just before another writer committed version 2.
    monkeypatch.setattr(
        store,
        "_load",
        lambda *a: store.Stored(real_load(*a).content, 1, real_load(*a).last_updated),
    )
    assert assert_outcome(client.put(url, json=created), 409)["issue"][0]["code"] == "conflict"


def test_concurrent_create_conflict_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import store

    body = {**fixture("Patient"), "id": "race"}
    client.put("/fhir/Patient/race", json=body)
    monkeypatch.setattr(store, "_load", lambda *a: None)  # other writer inserted after our read
    assert_outcome(client.put("/fhir/Patient/race", json=body), 409)
