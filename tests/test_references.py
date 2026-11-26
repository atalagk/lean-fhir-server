"""M4: referential integrity (F9)."""

from fastapi.testclient import TestClient
from helpers import assert_outcome, create, fixture

from app.config import Settings
from app.main import create_app


def encounter(subject: str, **extra) -> dict:
    return {**fixture("Encounter"), "subject": {"reference": subject}, **extra}


def test_unresolved_reference_422(client: TestClient) -> None:
    body = assert_outcome(client.post("/fhir/Encounter", json=encounter("Patient/missing")), 422)
    assert "Patient/missing" in body["issue"][0]["diagnostics"]
    put = client.put("/fhir/Encounter/e1", json={**encounter("Patient/missing"), "id": "e1"})
    assert_outcome(put, 422)
    assert_outcome(client.get("/fhir/Encounter/e1"), 404)  # nothing written


def test_resolved_reference_201(client: TestClient) -> None:
    pid = create(client, "Patient")
    assert client.post("/fhir/Encounter", json=encounter(f"Patient/{pid}")).status_code == 201


def test_all_unresolved_references_listed(client: TestClient) -> None:
    body = encounter(
        "Patient/p-x",
        serviceProvider={"reference": "Organization/o-x"},
        participant=[{"individual": {"reference": "Practitioner/pr-x"}}],
    )
    diag = assert_outcome(client.post("/fhir/Encounter", json=body), 422)["issue"][0]["diagnostics"]
    assert all(r in diag for r in ("Patient/p-x", "Organization/o-x", "Practitioner/pr-x"))


def test_absolute_url_under_base_is_checked(client: TestClient) -> None:
    assert_outcome(
        client.post("/fhir/Encounter", json=encounter("http://testserver/fhir/Patient/nope")), 422
    )
    pid = create(client, "Patient")
    resp = client.post("/fhir/Encounter", json=encounter(f"http://testserver/fhir/Patient/{pid}"))
    assert resp.status_code == 201


def test_unchecked_references_accepted(client: TestClient) -> None:
    for ref in (
        "Group/g1",  # unsupported type
        "https://other.example/fhir/Patient/1",  # external absolute URL
        "urn:uuid:7d1f2c43-0d55-4c9f-9a8e-2b1b6f0e2d11",
    ):
        assert client.post("/fhir/Encounter", json=encounter(ref)).status_code == 201, ref
    logical = {**fixture("Encounter"), "subject": {"identifier": {"system": "urn:x", "value": "1"}}}
    assert client.post("/fhir/Encounter", json=logical).status_code == 201


def test_unindexed_reference_not_checked(client: TestClient) -> None:
    # Encounter.episodeOfCare is not covered by a search parameter -> unchecked
    body = {**fixture("Encounter"), "episodeOfCare": [{"reference": "Organization/none"}]}
    assert client.post("/fhir/Encounter", json=body).status_code == 201


def test_self_reference_allowed(client: TestClient) -> None:
    body = {**fixture("Organization"), "id": "self1", "partOf": {"reference": "Organization/self1"}}
    assert client.put("/fhir/Organization/self1", json=body).status_code == 201
    assert client.delete("/fhir/Organization/self1").status_code == 204


def test_delete_referenced_409_then_204(client: TestClient) -> None:
    pid = create(client, "Patient")
    eid = create(client, "Encounter", {"subject": {"reference": f"Patient/{pid}"}})
    body = assert_outcome(client.delete(f"/fhir/Patient/{pid}"), 409)
    assert body["issue"][0]["code"] == "conflict"
    assert f"Encounter/{eid}" in body["issue"][0]["diagnostics"]
    assert client.get(f"/fhir/Patient/{pid}").status_code == 200
    assert client.delete(f"/fhir/Encounter/{eid}").status_code == 204
    assert client.delete(f"/fhir/Patient/{pid}").status_code == 204


def test_removing_reference_unblocks_delete(client: TestClient) -> None:
    oid = create(client, "Organization")
    pid = create(client, "Patient", {"managingOrganization": {"reference": f"Organization/{oid}"}})
    assert_outcome(client.delete(f"/fhir/Organization/{oid}"), 409)
    patient = client.get(f"/fhir/Patient/{pid}").json()
    del patient["managingOrganization"]
    client.put(f"/fhir/Patient/{pid}", json=patient)
    assert client.delete(f"/fhir/Organization/{oid}").status_code == 204


def test_enforcement_disabled(settings: Settings) -> None:
    s = settings.model_copy(update={"enforce_references": False})
    with TestClient(create_app(s)) as c:
        resp = c.post("/fhir/Encounter", json=encounter("Patient/missing"))
        assert resp.status_code == 201
        pid = create(c, "Patient")
        create(c, "Encounter", {"subject": {"reference": f"Patient/{pid}"}})
        assert c.delete(f"/fhir/Patient/{pid}").status_code == 204
        from app.db import metadata

        metadata.drop_all(c.app.state.engine)
