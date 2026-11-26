"""M3: Bundle shape, paging, errors, POST _search, index maintenance, reindex."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import assert_outcome, create, ids, search, search_ids
from sqlalchemy import func, select

from app import cli
from app.config import get_settings
from app.db import search_index


def test_bundle_shape(client: TestClient) -> None:
    rid = create(client, "Patient")
    bundle = search(client, "Patient", {"_id": rid})
    assert bundle["type"] == "searchset" and bundle["total"] == 1
    entry = bundle["entry"][0]
    assert entry["fullUrl"] == f"http://testserver/fhir/Patient/{rid}"
    assert entry["search"] == {"mode": "match"}
    assert entry["resource"] == client.get(f"/fhir/Patient/{rid}").json()
    assert [link["relation"] for link in bundle["link"]] == ["self"]


def test_empty_result_has_no_entry(client: TestClient) -> None:
    bundle = search(client, "Patient", {"family": "nobody"})
    assert bundle["total"] == 0 and "entry" not in bundle


def test_paging_45_patients(client: TestClient) -> None:
    created = {create(client, "Patient") for _ in range(45)}
    page = search(client, "Patient", {"_count": "20"})
    seen: list[str] = []
    pages = 0
    while True:
        pages += 1
        assert page["total"] == 45
        seen += ids(page)
        links = {link["relation"]: link["url"] for link in page["link"]}
        if pages > 1:
            assert "previous" in links
        if "next" not in links:
            break
        resp = client.get(links["next"])
        assert resp.status_code == 200
        page = resp.json()
    assert pages == 3
    assert len(seen) == 45 and set(seen) == created  # no duplicates or omissions
    assert len(ids(page)) == 5


def test_paging_links_keep_filters(client: TestClient) -> None:
    for _ in range(3):
        create(client, "Patient", {"gender": "female"})
    create(client, "Patient", {"gender": "male"})
    page = search(client, "Patient", {"gender": "female", "_count": "2"})
    nxt = next(link["url"] for link in page["link"] if link["relation"] == "next")
    assert "gender=female" in nxt
    assert len(ids(client.get(nxt).json())) == 1


def test_count_zero_returns_total_only(client: TestClient) -> None:
    for _ in range(3):
        create(client, "Patient")
    bundle = search(client, "Patient", {"_count": "0"})
    assert bundle["total"] == 3 and "entry" not in bundle


def test_count_capped_at_max(client: TestClient, app: FastAPI) -> None:
    app.state.settings.max_count = 5
    for _ in range(7):
        create(client, "Patient")
    bundle = search(client, "Patient", {"_count": "1000"})
    assert len(bundle["entry"]) == 5 and bundle["total"] == 7
    assert "_count=5" in bundle["link"][0]["url"]


def test_default_count(client: TestClient, app: FastAPI) -> None:
    app.state.settings.default_count = 2
    for _ in range(3):
        create(client, "Patient")
    assert len(search(client, "Patient", {})["entry"]) == 2


def test_unknown_param_and_modifier_400(client: TestClient) -> None:
    cases = {
        "foo=bar": "foo",
        "name:fuzzy=x": "fuzzy",
        "gender:text=f": "text",
        "_include=Patient:organization": "_include",
        "subject:Patient=1": "Patient",
        "_count=abc": "_count",
        "_count=1&_count=2": "_count",
        "_sort=family": "_sort",
        "birthdate=sa2000": "sa",
        "birthdate=notadate": "birthdate",
        "birthdate=2024-13": "birthdate",
        "identifier=|": "identifier",
        "family=": "family",
        "identifier=a|b|c": "identifier",
    }
    for query, name in cases.items():
        rtype = "Encounter" if query.startswith("subject") else "Patient"
        body = assert_outcome(client.get(f"/fhir/{rtype}?{query}"), 400)
        assert name in body["issue"][0]["diagnostics"], query


def test_param_of_other_type_rejected(client: TestClient) -> None:
    assert_outcome(client.get("/fhir/Practitioner?birthdate=2000"), 400)


def test_format_json_allowed_in_search(client: TestClient) -> None:
    create(client, "Patient")
    assert search(client, "Patient", {"_format": "json"})["total"] == 1


def test_post_search_matches_get(client: TestClient) -> None:
    for fam in ("Smith", "Smythe", "Jones"):
        create(client, "Patient", {"name": [{"family": fam}], "gender": "female"})
    query = "family=sm&gender=female&_count=1"
    get = client.get(f"/fhir/Patient?{query}").json()
    post = client.post(
        "/fhir/Patient/_search",
        content=query,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert post.status_code == 200
    assert post.json() == get
    assert get["total"] == 2


def test_post_search_errors(client: TestClient) -> None:
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    assert_outcome(client.post("/fhir/Patient/_search", content="foo=1", headers=form), 400)
    assert_outcome(client.post("/fhir/Patient/_search", json={"family": "x"}), 415)
    assert_outcome(client.post("/fhir/Patient/_search", content=b"family=\xff", headers=form), 400)


def _index_count(app: FastAPI, rid: str) -> int:
    with app.state.engine.connect() as conn:
        return conn.execute(
            select(func.count()).select_from(search_index).where(search_index.c.resource_id == rid)
        ).scalar_one()


def test_update_changes_index(client: TestClient) -> None:
    rid = create(client, "Patient", {"name": [{"family": "Before"}]})
    body = client.get(f"/fhir/Patient/{rid}").json()
    client.put(f"/fhir/Patient/{rid}", json={**body, "name": [{"family": "After"}]})
    assert search_ids(client, "Patient", {"family": "before"}) == set()
    assert search_ids(client, "Patient", {"family": "after"}) == {rid}


def test_delete_removes_index_rows(client: TestClient, app: FastAPI) -> None:
    rid = create(client, "Patient", {"name": [{"family": "Gone"}]})
    assert _index_count(app, rid) > 0
    client.delete(f"/fhir/Patient/{rid}")
    assert _index_count(app, rid) == 0
    assert search_ids(client, "Patient", {"family": "gone"}) == set()


def test_index_write_is_atomic(client: TestClient, monkeypatch) -> None:
    from app import store

    def boom(*_a, **_k):
        raise RuntimeError("index failure")

    monkeypatch.setattr(store, "index_rows", boom)
    resp = client.put("/fhir/Patient/atomic", json={"resourceType": "Patient", "id": "atomic"})
    assert_outcome(resp, 500)
    monkeypatch.undo()
    assert_outcome(client.get("/fhir/Patient/atomic"), 404)


def test_reindex_leaves_results_unchanged(
    client: TestClient, app: FastAPI, settings, monkeypatch, capsys
) -> None:
    from helpers import fixture

    create(client, "Patient", fixture("Patient-rich"))
    p = create(client, "Patient", rid="p1")
    create(client, "Organization", {"alias": ["Zed"]})
    create(
        client,
        "Encounter",
        {"subject": {"reference": f"Patient/{p}"}, "type": [{"coding": [{"code": "x"}]}]},
    )
    queries = [
        ("Patient", "family=smi"),
        ("Patient", "identifier=https://example.org/mrn|MRN-0001"),
        ("Patient", "birthdate=1975"),
        ("Organization", "name=zed"),
        ("Encounter", "patient=p1"),
        ("Encounter", "date=ge2024"),
        ("Encounter", "type=x"),
    ]
    before = {q: search(client, *q) for q in queries}
    with app.state.engine.connect() as conn:
        rows_before = conn.execute(select(func.count()).select_from(search_index)).scalar_one()

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    get_settings.cache_clear()
    try:
        assert cli.main(["reindex"]) == 0
    finally:
        get_settings.cache_clear()
    assert '"reindexed": 4' in capsys.readouterr().out

    with app.state.engine.connect() as conn:
        rows_after = conn.execute(select(func.count()).select_from(search_index)).scalar_one()
    assert rows_after == rows_before > 0
    for q in queries:
        assert search(client, *q) == before[q], q
        assert before[q]["total"] >= 1, q
