"""M3: token, string, date and reference matching semantics; AND/OR; _id; _sort."""

import time

from fastapi.testclient import TestClient
from helpers import create, search, search_ids


def test_token_identifier_forms(client: TestClient) -> None:
    with_sys = create(client, "Patient", {"identifier": [{"system": "urn:mrn", "value": "X1"}]})
    no_sys = create(client, "Patient", {"identifier": [{"value": "X1"}]})
    other = create(client, "Patient", {"identifier": [{"system": "urn:nhi", "value": "Y2"}]})
    assert search_ids(client, "Patient", {"identifier": "urn:mrn|X1"}) == {with_sys}
    assert search_ids(client, "Patient", {"identifier": "X1"}) == {with_sys, no_sys}
    assert search_ids(client, "Patient", {"identifier": "|X1"}) == {no_sys}
    assert search_ids(client, "Patient", {"identifier": "urn:mrn|"}) == {with_sys}
    assert search_ids(client, "Patient", {"identifier": "urn:nhi|"}) == {other}
    assert search_ids(client, "Patient", {"identifier": "x1"}) == set()  # case-sensitive


def test_token_gender_and_active(client: TestClient) -> None:
    f = create(client, "Patient", {"gender": "female", "active": True})
    m = create(client, "Patient", {"gender": "male", "active": False})
    assert search_ids(client, "Patient", {"gender": "female"}) == {f}
    assert search_ids(client, "Patient", {"active": "true"}) == {f}
    assert search_ids(client, "Patient", {"active": "false"}) == {m}


def test_token_escaped_separator(client: TestClient) -> None:
    rid = create(client, "Patient", {"identifier": [{"system": "urn:a", "value": "a|b,c"}]})
    assert search_ids(client, "Patient", {"identifier": r"urn:a|a\|b\,c"}) == {rid}


def test_string_starts_with_case_insensitive(client: TestClient) -> None:
    smith = create(client, "Patient", {"name": [{"family": "Smith"}]})
    smithson = create(client, "Patient", {"name": [{"family": "SMITHSON"}]})
    jones = create(client, "Patient", {"name": [{"family": "Jones"}]})
    assert search_ids(client, "Patient", {"family": "smi"}) == {smith, smithson}
    assert jones not in search_ids(client, "Patient", {"family": "smi"})
    assert search_ids(client, "Patient", {"family:exact": "Smith"}) == {smith}
    assert search_ids(client, "Patient", {"family:exact": "smith"}) == set()
    assert search_ids(client, "Patient", {"name:contains": "mit"}) == {smith, smithson}
    assert search_ids(client, "Patient", {"name:contains": "one"}) == {jones}


def test_string_like_wildcards_are_literal(client: TestClient) -> None:
    create(client, "Patient", {"name": [{"family": "Smith"}]})
    pct = create(client, "Patient", {"name": [{"family": "50%_off"}]})
    assert search_ids(client, "Patient", {"family": "%"}) == set()
    assert search_ids(client, "Patient", {"family:contains": "%_"}) == {pct}


def test_birthdate(client: TestClient) -> None:
    b79 = create(client, "Patient", {"birthDate": "1979-12-31"})
    b80 = create(client, "Patient", {"birthDate": "1980-06-15"})
    b89 = create(client, "Patient", {"birthDate": "1989-12-31"})
    b90 = create(client, "Patient", {"birthDate": "1990-01-01"})
    assert search_ids(client, "Patient", {"birthdate": "1980"}) == {b80}
    q = [("birthdate", "ge1980-01-01"), ("birthdate", "lt1990")]
    assert search_ids(client, "Patient", q) == {b80, b89}
    assert search_ids(client, "Patient", {"birthdate": "gt1989"}) == {b90}
    assert search_ids(client, "Patient", {"birthdate": "le1979-12-31"}) == {b79}
    assert search_ids(client, "Patient", {"birthdate": "eq1980-06"}) == {b80}


def test_encounter_date_periods(client: TestClient) -> None:
    closed = create(
        client,
        "Encounter",
        {"period": {"start": "2024-03-01T09:00:00Z", "end": "2024-03-01T10:00:00Z"}},
    )
    open_end = create(client, "Encounter", {"period": {"start": "2024-01-10"}})
    open_start = create(client, "Encounter", {"period": {"end": "2023-06-30"}})
    q = lambda v: search_ids(client, "Encounter", {"date": v})  # noqa: E731
    assert q("2024-03-01") == {closed}
    assert q("2024") == {closed}  # open-ended periods never fall *within* a range
    assert q("ge2025-01-01") == {open_end}
    assert q("gt2024-03-01") == {open_end}
    assert q("le2023-01-01") == {open_start}
    assert q("lt2024-01-10") == {open_start}
    assert q("ge2024-01-10") == {closed, open_end}
    assert q("le2024-03-01") == {closed, open_end, open_start}


def test_date_timezone_handling(client: TestClient) -> None:
    # 2024-03-01T23:30-05:00 == 2024-03-02T04:30Z
    rid = create(
        client,
        "Encounter",
        {"period": {"start": "2024-03-01T23:30:00-05:00", "end": "2024-03-01T23:45:00-05:00"}},
    )
    assert search_ids(client, "Encounter", {"date": "2024-03-02"}) == {rid}
    assert search_ids(client, "Encounter", {"date": "2024-03-01"}) == set()
    assert search_ids(client, "Encounter", {"date": "ge2024-03-02T04:30:00Z"}) == {rid}
    assert search_ids(client, "Encounter", {"date": "2024-03-02T04:30"}) == set()  # 15 min > 1 min


def test_reference_forms(client: TestClient) -> None:
    p = create(client, "Patient", rid="p1")
    e = create(client, "Encounter", {"subject": {"reference": "Patient/p1"}})
    other = create(client, "Encounter", {"subject": {"reference": "Patient/p2"}})
    for value in ("Patient/p1", "p1", "http://testserver/fhir/Patient/p1"):
        assert search_ids(client, "Encounter", {"patient": value}) == {e}, value
        assert search_ids(client, "Encounter", {"subject": value}) == {e}, value
    assert other not in search_ids(client, "Encounter", {"patient": p})


def test_absolute_reference_in_resource_is_normalised(client: TestClient) -> None:
    e = create(client, "Encounter", {"subject": {"reference": "http://testserver/fhir/Patient/p1"}})
    assert search_ids(client, "Encounter", {"patient": "Patient/p1"}) == {e}


def test_subject_vs_patient_target(client: TestClient) -> None:
    g = create(client, "Encounter", {"subject": {"reference": "Group/g1"}})
    assert search_ids(client, "Encounter", {"subject": "Group/g1"}) == {g}
    assert search_ids(client, "Encounter", {"patient": "Group/g1"}) == set()
    assert search_ids(client, "Encounter", {"patient": "g1"}) == set()
    assert (
        search_ids(client, "Encounter", {"subject": "g1"}) == set()
    )  # bare id: supported types only


def test_and_or_semantics(client: TestClient) -> None:
    a = create(client, "Patient", {"gender": "female", "name": [{"family": "Alpha"}]})
    b = create(client, "Patient", {"gender": "male", "name": [{"family": "Beta"}]})
    c = create(client, "Patient", {"gender": "female", "name": [{"family": "Gamma"}]})
    assert search_ids(client, "Patient", {"family": "alpha,beta"}) == {a, b}
    assert search_ids(client, "Patient", [("gender", "female"), ("family", "alpha,gamma")]) == {
        a,
        c,
    }
    assert search_ids(client, "Patient", [("family", "alpha"), ("family", "beta")]) == set()
    assert search_ids(client, "Patient", [("gender", "female"), ("family", "beta")]) == set()


def test_id_comma_list_resolves_encounters(client: TestClient) -> None:
    """SPEC section 8: resolve all encounters of a composition in one call."""
    e1, e2, e3 = (create(client, "Encounter") for _ in range(3))
    bundle = search(client, "Encounter", f"_id={e1},{e3},missing-id")
    assert {e["resource"]["id"] for e in bundle["entry"]} == {e1, e3}
    assert bundle["total"] == 2
    assert e2 not in search_ids(client, "Encounter", {"_id": f"{e1},{e3}"})


def test_sort_and_last_updated(client: TestClient) -> None:
    first = create(client, "Patient")
    time.sleep(0.01)
    second = create(client, "Patient")
    lu = client.get(f"/fhir/Patient/{second}").json()["meta"]["lastUpdated"]
    default = [e["resource"]["id"] for e in search(client, "Patient", {})["entry"]]
    assert default == [second, first]
    asc = [
        e["resource"]["id"] for e in search(client, "Patient", {"_sort": "_lastUpdated"})["entry"]
    ]
    assert asc == [first, second]
    desc = search(client, "Patient", {"_sort": "-_lastUpdated"})["entry"]
    assert [e["resource"]["id"] for e in desc] == [second, first]
    assert search_ids(client, "Patient", {"_lastUpdated": f"ge{lu}"}) == {second}
    assert search_ids(client, "Patient", {"_lastUpdated": f"lt{lu}"}) == {first}
