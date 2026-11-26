"""M4: conditional create (F3)."""

from fastapi.testclient import TestClient
from helpers import assert_outcome, create, fixture, search_ids

IDENT = {"identifier": [{"system": "urn:test:mrn", "value": "M-1"}]}
HEADER = {"If-None-Exist": "identifier=urn:test:mrn|M-1"}


def test_conditional_create_first_201_then_200(client: TestClient) -> None:
    body = {**fixture("Patient"), **IDENT}
    first = client.post("/fhir/Patient", json=body, headers=HEADER)
    assert first.status_code == 201
    assert first.headers["etag"] == 'W/"1"'
    rid = first.json()["id"]

    second = client.post("/fhir/Patient", json=body, headers=HEADER)
    assert second.status_code == 200
    assert second.json() == first.json()  # same id, no new version
    assert second.headers["etag"] == 'W/"1"'
    assert "location" not in second.headers
    assert client.get(f"/fhir/Patient/{rid}").json()["meta"]["versionId"] == "1"
    assert search_ids(client, "Patient", {"identifier": "urn:test:mrn|M-1"}) == {rid}


def test_conditional_create_multiple_matches_412(client: TestClient) -> None:
    create(client, "Patient", IDENT)
    create(client, "Patient", IDENT)
    resp = client.post("/fhir/Patient", json={**fixture("Patient"), **IDENT}, headers=HEADER)
    assert assert_outcome(resp, 412)["issue"][0]["code"] == "duplicate"
    assert len(search_ids(client, "Patient", {"identifier": "urn:test:mrn|M-1"})) == 2


def test_conditional_create_uses_search_parser(client: TestClient) -> None:
    rid = create(client, "Patient", {"name": [{"family": "Smith"}], "gender": "female"})
    headers = {"If-None-Exist": "family=smi&gender=female"}
    resp = client.post("/fhir/Patient", json=fixture("Patient"), headers=headers)
    assert resp.status_code == 200 and resp.json()["id"] == rid
    headers = {"If-None-Exist": "?family=smi&gender=male"}  # leading '?' tolerated
    assert client.post("/fhir/Patient", json=fixture("Patient"), headers=headers).status_code == 201


def test_conditional_create_bad_query_400(client: TestClient) -> None:
    for value in ("foo=bar", "family:fuzzy=x", "", "birthdate=notadate"):
        resp = client.post(
            "/fhir/Patient", json=fixture("Patient"), headers={"If-None-Exist": value}
        )
        assert_outcome(resp, 400)


def test_conditional_create_still_validates(client: TestClient) -> None:
    resp = client.post("/fhir/Patient", json={**fixture("Patient"), "gender": 5}, headers=HEADER)
    assert_outcome(resp, 422)
