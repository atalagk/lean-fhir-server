"""M3: every search parameter in SPEC 2.4 has a positive and a negative test."""

import pytest
from fastapi.testclient import TestClient
from helpers import create, search_ids

from app.registry import REGISTRY

IDENT = {"identifier": [{"system": "urn:test:ids", "value": "ID-1"}]}
NAME = {"name": [{"family": "Smith", "given": ["Jane"], "prefix": ["Dr"], "text": "Jane Smith"}]}
CC = {"coding": [{"system": "urn:test:cs", "code": "c1"}]}


def ref(r: str) -> dict:
    return {"reference": r}


# (type, param, extra body, matching query value, non-matching query value)
CASES = [
    ("Patient", "identifier", IDENT, "urn:test:ids|ID-1", "urn:test:ids|ID-2"),
    ("Patient", "name", NAME, "jan", "bob"),
    ("Patient", "family", NAME, "smi", "jan"),
    ("Patient", "given", NAME, "jan", "smi"),
    ("Patient", "birthdate", {"birthDate": "1980-05-01"}, "1980", "1981"),
    ("Patient", "gender", {"gender": "female"}, "female", "male"),
    ("Patient", "active", {"active": True}, "true", "false"),
    (
        "Patient",
        "telecom",
        {"telecom": [{"system": "email", "value": "a@b.example"}]},
        "email|a@b.example",
        "phone|a@b.example",
    ),
    (
        "Patient",
        "organization",
        {"managingOrganization": ref("Organization/o1")},
        "Organization/o1",
        "Organization/o2",
    ),
    (
        "Patient",
        "general-practitioner",
        {"generalPractitioner": [ref("Practitioner/pr1")]},
        "pr1",
        "pr2",
    ),
    ("Practitioner", "identifier", IDENT, "ID-1", "ID-2"),
    ("Practitioner", "name", NAME, "dr", "mr"),
    ("Practitioner", "family", NAME, "smith", "jane"),
    ("Practitioner", "given", NAME, "jane", "smith"),
    ("Practitioner", "active", {"active": False}, "false", "true"),
    ("PractitionerRole", "identifier", IDENT, "urn:test:ids|", "urn:other|"),
    (
        "PractitionerRole",
        "practitioner",
        {"practitioner": ref("Practitioner/pr1")},
        "Practitioner/pr1",
        "Practitioner/pr2",
    ),
    ("PractitionerRole", "organization", {"organization": ref("Organization/o1")}, "o1", "o2"),
    (
        "PractitionerRole",
        "location",
        {"location": [ref("Location/l1")]},
        "Location/l1",
        "Location/l2",
    ),
    ("PractitionerRole", "role", {"code": [CC]}, "urn:test:cs|c1", "urn:test:cs|c2"),
    ("PractitionerRole", "specialty", {"specialty": [CC]}, "c1", "c2"),
    ("PractitionerRole", "active", {"active": False}, "false", "true"),
    ("Organization", "identifier", IDENT, "ID-1", "ID-2"),
    ("Organization", "name", {"name": "Acme", "alias": ["Zeta Clinic"]}, "zeta", "beta"),
    ("Organization", "type", {"type": [CC]}, "urn:test:cs|c1", "c2"),
    ("Organization", "active", {"active": False}, "false", "true"),
    (
        "Organization",
        "partof",
        {"partOf": ref("Organization/o1")},
        "Organization/o1",
        "Organization/o2",
    ),
    ("Location", "identifier", IDENT, "ID-1", "ID-2"),
    ("Location", "name", {"name": "Ward 9", "alias": ["Blue Ward"]}, "blue", "red"),
    ("Location", "status", {"status": "suspended"}, "suspended", "inactive"),
    ("Location", "type", {"type": [CC]}, "c1", "c2"),
    ("Location", "organization", {"managingOrganization": ref("Organization/o1")}, "o1", "o2"),
    ("Location", "partof", {"partOf": ref("Location/l1")}, "Location/l1", "Location/l2"),
    ("Encounter", "identifier", IDENT, "ID-1", "ID-2"),
    ("Encounter", "subject", {"subject": ref("Patient/p1")}, "Patient/p1", "Patient/p2"),
    ("Encounter", "patient", {"subject": ref("Patient/p1")}, "p1", "p2"),
    ("Encounter", "status", {"status": "in-progress"}, "in-progress", "cancelled"),
    ("Encounter", "class", {}, "http://terminology.hl7.org/CodeSystem/v3-ActCode|AMB", "IMP"),
    ("Encounter", "type", {"type": [CC]}, "c1", "c2"),
    ("Encounter", "date", {"period": {"start": "2024-03-01T09:00:00Z"}}, "ge2024-03", "lt2024-03"),
    (
        "Encounter",
        "participant",
        {"participant": [{"individual": ref("Practitioner/pr1")}]},
        "pr1",
        "pr2",
    ),
    (
        "Encounter",
        "location",
        {"location": [{"location": ref("Location/l1")}]},
        "Location/l1",
        "l2",
    ),
    ("Encounter", "service-provider", {"serviceProvider": ref("Organization/o1")}, "o1", "o2"),
    ("Encounter", "part-of", {"partOf": ref("Encounter/e1")}, "Encounter/e1", "Encounter/e2"),
]


@pytest.fixture
def targets(client: TestClient) -> None:
    """Referenced resources exist (referential integrity is enforced from M4)."""
    create(client, "Patient", rid="p1")
    create(client, "Practitioner", rid="pr1")
    create(client, "Organization", rid="o1")
    create(client, "Location", rid="l1")
    create(client, "Encounter", rid="e1")


def test_every_registry_param_is_covered() -> None:
    covered = {(t, p) for t, p, *_ in CASES}
    assert covered == {(t, p.name) for t, rdef in REGISTRY.items() for p in rdef.params}


@pytest.mark.parametrize(
    ("rtype", "param", "extra", "hit", "miss"), CASES, ids=lambda v: str(v)[:30]
)
def test_param(client: TestClient, targets: None, rtype, param, extra, hit, miss) -> None:
    rid = create(client, rtype, extra)
    assert rid in search_ids(client, rtype, {param: hit})
    assert rid not in search_ids(client, rtype, {param: miss})


@pytest.mark.parametrize("rtype", list(REGISTRY))
def test_id_and_last_updated(client: TestClient, rtype: str) -> None:
    rid = create(client, rtype)
    other = create(client, rtype)
    assert search_ids(client, rtype, {"_id": rid}) == {rid}
    assert search_ids(client, rtype, {"_id": "nope"}) == set()
    assert rid in search_ids(client, rtype, {"_lastUpdated": "ge2000-01-01"})
    assert search_ids(client, rtype, {"_lastUpdated": "lt2000-01-01"}) == set()
    assert {rid, other} <= search_ids(client, rtype, {"_lastUpdated": "le2999"})
