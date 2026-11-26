# LeanFHIR — Lean FHIR Demographic & Admin Server

**Specification v0.1 — functional spec, non-functional spec, architecture, build plan**

Status: PoC / MVP grade. Not for production. Synthetic or de-identified data only.

---

## 1. Purpose and scope

### 1.1 Problem

Projects building openEHR-based clinical apps need demographic and basic administrative data (patients, practitioners, organisations, locations, encounters). The openEHR demographic model is rarely implemented; common practice is to keep demographics in FHIR. Deploying a full FHIR server/CDR (HAPI FHIR etc.) for six resource types is disproportionate for a PoC or MVP.

### 1.2 Solution

A single small Python service exposing a deliberately limited subset of the FHIR R4 REST API for six resource types, backed by SQLite (default) or PostgreSQL, running in one Docker container.

### 1.3 Primary use case

An openEHR clinical app (e.g. on EHRbase) uses LeanFHIR as its demographic/admin source:

1. App creates/looks up a `Patient` in LeanFHIR.
2. App creates the EHR in the openEHR CDR with `EHR_STATUS.subject.external_ref` pointing at the FHIR `Patient.id`.
3. App creates `Encounter`s in LeanFHIR and references them from compositions.
4. App resolves practitioners, organisations and locations from LeanFHIR for display and for composition context.

LeanFHIR is **standalone**: it has no knowledge of, or connection to, the openEHR CDR. Linkage is the app's responsibility (pattern in section 8).

### 1.4 Decisions already made

| # | Decision |
|---|---|
| D1 | Resources: Patient, Practitioner, PractitionerRole, Organization, Location, Encounter. Nothing else. |
| D2 | SQLite by default; PostgreSQL selectable by connection string only (no code change). |
| D3 | Standalone; no EHRbase hook. |
| D4 | No authentication in v0.1. A single no-op auth seam is left for later. |
| D5 | Current version only. No `_history`, no vread. |
| D6 | No transaction/batch Bundles, no seed loader, no declared profile conformance. |

### 1.5 Non-goals (do not build)

- Profile validation, terminology validation, ValueSet/CodeSystem operations
- `_history`, vread, patch, conditional update/delete
- Bundle transaction/batch, bulk export, `$everything`, `$match`, any `$operation`
- `_include`, `_revinclude`, chained search, `_has`, composite params, `:missing`, `:not`, `_filter`
- XML, GraphQL, Subscriptions, SMART on FHIR, AuditEvent/Provenance
- Authentication/authorisation, multi-tenancy
- Database migrations tooling (Alembic), async DB drivers, caching layers, message queues
- Any UI

If a requirement is not in this document, it is out of scope.

---

## 2. Functional specification

### 2.1 General

| Item | Requirement |
|---|---|
| FHIR version | R4 (declared `fhirVersion` 4.0.1). See 5.3 for the R4/R4B validation note. |
| Base path | `/fhir` |
| Format | JSON only. Response `Content-Type: application/fhir+json; charset=utf-8`. Accept request bodies as `application/fhir+json` or `application/json`. `_format=xml` or `Accept: application/fhir+xml` only → 406. |
| IDs | Server-assigned UUID v4 on POST. Client-assigned on PUT, must match `[A-Za-z0-9\-\.]{1,64}`. |
| Errors | Every non-2xx response body is an `OperationOutcome`. |
| Return body | Create/update always return the full stored resource (ignore `Prefer`). |

### 2.2 Interactions (identical for all six resource types)

| ID | Interaction | Request | Success | Notes |
|---|---|---|---|---|
| F1 | read | `GET /fhir/{type}/{id}` | 200 + resource, `ETag: W/"{versionId}"`, `Last-Modified` | 404 if absent |
| F2 | create | `POST /fhir/{type}` | 201 + resource, `Location: {base}/{type}/{id}`, `ETag` | Any `id` in the body is ignored |
| F3 | conditional create | `POST /fhir/{type}` + `If-None-Exist: {search query}` | 0 matches → as F2; 1 match → 200 + existing resource, nothing written; >1 → 412 | Query uses the same parser as search |
| F4 | update | `PUT /fhir/{type}/{id}` | 200 (existing) or 201 (update-as-create) + resource, `ETag` | Body `id` must be present and equal URL id, else 400. Optional `If-Match: W/"n"`; mismatch → 412 |
| F5 | delete | `DELETE /fhir/{type}/{id}` | 204 | Hard delete. Absent → 204 (idempotent). Referenced by another resource → 409 (see F9) |
| F6 | search | `GET /fhir/{type}?…` and `POST /fhir/{type}/_search` (form-encoded) | 200 + `Bundle` type `searchset` | See 2.4 |
| F7 | capabilities | `GET /fhir/metadata` | 200 + `CapabilityStatement` | Generated from the registry (5.2), not hand-written |
| F8 | health | `GET /health` | 200 `{"status":"ok"}` | Includes a trivial DB query; 503 on failure. Not a FHIR endpoint |

Unsupported resource type → 404. Unsupported method on a supported path → 405.

### 2.3 Write rules

- **F9 Referential integrity** (config `ENFORCE_REFERENCES`, default `true`):
  - On create/update, every indexed reference (i.e. one covered by a reference search parameter) of the form `{Type}/{id}`, where `Type` is one of the six supported types, must resolve to an existing resource; otherwise 422. Absolute URLs starting with the server base URL are normalised to relative form first. References to unsupported types, other absolute URLs, `urn:` references, and logical references (identifier only) are stored unchecked.
  - On delete, if any other resource holds an indexed reference to the target → 409.
- **F10 Validation**: body must be valid JSON (else 400), `resourceType` must equal the URL type (else 400), and must pass structural validation against the FHIR model (else 422, with one `OperationOutcome.issue` per validation error including `expression`/location). No profile, cardinality-beyond-base, or terminology checks.
- **F11 Meta**: server sets `meta.versionId` (integer as string, starts at "1", +1 per update) and `meta.lastUpdated` (UTC, millisecond precision). Client-supplied values for these two are overwritten. Other `meta` content (`profile`, `tag`, `security`) is stored as supplied.
- **F12 Atomicity**: resource write and its search-index rows are committed in one DB transaction.

### 2.4 Search

**Result bundle**: `type=searchset`, accurate `total`, `link` with `self`, `next`, `previous` as applicable, `entry[].fullUrl = {base}/{type}/{id}`, `entry[].search.mode = "match"`.

**Control parameters**

| Param | Behaviour |
|---|---|
| `_count` | Default 20, max 200 (both configurable). `_count=0` returns total only. |
| `_offset` | Zero-based offset; used in paging links. (Non-standard but simple; stateless.) |
| `_sort` | `_lastUpdated` or `-_lastUpdated` only. Default order: `lastUpdated` descending, then `id`. |
| `_id` | Token match on logical id. |
| `_lastUpdated` | Date param with prefixes. |

**Combination semantics**: repeated parameter = AND; comma-separated values = OR. 

**Unknown or unsupported parameter or modifier → 400** with an `OperationOutcome` naming it. (Deliberate: silently ignoring a filter and returning all patients is the worse failure mode in a PoC.)

**Parameter type semantics**

| Type | Matching |
|---|---|
| token | `code`, `system|code`, `|code` (no system), `system|` (any code in system). Case-sensitive exact match. Booleans index as `true`/`false`. No modifiers. |
| string | Case-insensitive starts-with by default. Modifiers `:exact` (case-sensitive full match) and `:contains` supported. |
| date | Prefixes `eq` (default), `ge`, `gt`, `le`, `lt`. Both stored values and search values are treated as ranges according to their precision (`2024` = whole year). `eq`: stored range within search range. `ge`: stored end ≥ search start. `gt`: stored end > search end. `le`: stored start ≤ search end. `lt`: stored start < search start. Missing Period bounds are open-ended. Values without timezone are treated as UTC. |
| reference | `{Type}/{id}`, bare `{id}`, or absolute URL under the server base. No chaining, no `:identifier`. |

**Supported search parameters** (names and meaning follow the FHIR R4 standard search parameters)

| Resource | Parameter | Type | Indexed from |
|---|---|---|---|
| Patient | `identifier` | token | `identifier` |
| | `name` | string | `name.family`, `name.given`, `name.text`, `name.prefix`, `name.suffix` |
| | `family` | string | `name.family` |
| | `given` | string | `name.given` |
| | `birthdate` | date | `birthDate` |
| | `gender` | token | `gender` |
| | `active` | token | `active` |
| | `telecom` | token | `telecom` (system = ContactPoint.system, code = value) |
| | `organization` | reference | `managingOrganization` |
| | `general-practitioner` | reference | `generalPractitioner` |
| Practitioner | `identifier` | token | `identifier` |
| | `name`, `family`, `given` | string | as Patient |
| | `active` | token | `active` |
| PractitionerRole | `identifier` | token | `identifier` |
| | `practitioner` | reference | `practitioner` |
| | `organization` | reference | `organization` |
| | `location` | reference | `location` |
| | `role` | token | `code` |
| | `specialty` | token | `specialty` |
| | `active` | token | `active` |
| Organization | `identifier` | token | `identifier` |
| | `name` | string | `name`, `alias` |
| | `type` | token | `type` |
| | `active` | token | `active` |
| | `partof` | reference | `partOf` |
| Location | `identifier` | token | `identifier` |
| | `name` | string | `name`, `alias` |
| | `status` | token | `status` |
| | `type` | token | `type` |
| | `organization` | reference | `managingOrganization` |
| | `partof` | reference | `partOf` |
| Encounter | `identifier` | token | `identifier` |
| | `subject` | reference | `subject` |
| | `patient` | reference | `subject` where target type is Patient |
| | `status` | token | `status` |
| | `class` | token | `class` (Coding) |
| | `type` | token | `type` |
| | `date` | date | `period` |
| | `participant` | reference | `participant.individual` |
| | `location` | reference | `location.location` |
| | `service-provider` | reference | `serviceProvider` |
| | `part-of` | reference | `partOf` |

All types additionally support `_id` and `_lastUpdated`.

### 2.5 HTTP status summary

| Status | When |
|---|---|
| 200 | read, update of existing, search, conditional create with one match |
| 201 | create, update-as-create |
| 204 | delete |
| 400 | malformed JSON, resourceType/URL mismatch, id mismatch, bad id format, unknown search param/modifier, bad search value |
| 404 | unknown resource type or id |
| 405 | unsupported method |
| 406 | non-JSON format requested |
| 409 | delete blocked by inbound references |
| 412 | `If-Match` version mismatch; conditional create with multiple matches |
| 422 | structural validation failure; unresolved reference |
| 500 | unhandled error (generic `OperationOutcome`, no stack trace in body) |

---

## 3. Non-functional specification

Targets are PoC-grade and indicative, not SLAs.

| ID | Area | Requirement |
|---|---|---|
| N1 | Deployment | One container. `docker compose up` gives a working server with zero configuration. SQLite file on a named volume. |
| N2 | Footprint | Image ≤ 300 MB (python:3.12-slim base). Idle RSS ≤ 200 MB. Cold start ≤ 5 s. |
| N3 | Scale envelope | Designed for ≤ 100k resources total and a handful of concurrent users. Indicative on a laptop at that volume: read p95 < 50 ms, indexed search p95 < 300 ms. No load-test deliverable; one smoke benchmark script is optional. |
| N4 | Concurrency | SQLite: WAL mode, `busy_timeout` 5 s, single Uvicorn worker process. PostgreSQL: default pool, workers configurable. Optimistic locking via `If-Match`. |
| N5 | Portability | Identical behaviour on SQLite and PostgreSQL. No dialect-specific SQL or JSON operators; all search goes through the index table. The test suite runs against both. |
| N6 | Configuration | Environment variables only (section 6). Sensible defaults for all. |
| N7 | Security | No auth (D4). Must be documented prominently in README: run only on a trusted network / behind a gateway; no real patient data. Container runs as non-root. CORS disabled unless configured. No stack traces in responses. All SQL parameterised via SQLAlchemy. |
| N8 | Extensibility seams | (a) One FastAPI dependency `require_auth` (no-op) applied to the FHIR router, so API-key/OIDC can be added in one place. (b) Adding a resource type or search parameter = registry entry only (5.2). No other seams. |
| N9 | Observability | Structured (JSON) logs to stdout: method, path, status, duration, resource type/id. Do not log request bodies. `GET /health` for container healthcheck. |
| N10 | Data durability | Whatever the DB gives. Backup = copy the SQLite file / `pg_dump`. No schema migration support: schema changes between versions may require a fresh DB. A `reindex` CLI command rebuilds the search index from stored resources. |
| N11 | Testability | pytest suite covering every acceptance criterion in section 9, runnable locally and in the container, in-memory/temp SQLite by default. Target ≥ 85% line coverage of `app/`. |
| N12 | Maintainability | Target ≤ ~1,500 lines of application code. Type hints throughout. Ruff for lint/format. Pinned dependencies. |
| N13 | Documentation | README: quick start, config table, supported interactions/search params, the openEHR linkage pattern (section 8), known deviations from FHIR (section 7), curl examples. Auto-generated OpenAPI at `/docs` is a bonus, not a deliverable. |

---

## 4. Architecture

### 4.1 Overview

```
            ┌──────────────────────── container: leanfhir ────────────────────────┐
 openEHR    │                                                                     │
 clinical ──┼─► Uvicorn ─► FastAPI app                                            │
 app / curl │               │                                                     │
            │               ├─ routes.py    generic FHIR router (/fhir/{type}…)   │
            │               │      │                                              │
            │               │      ├─ validation  (fhir.resources models)         │
            │               │      ├─ search.py   (query string → SQL)            │
            │               │      └─ store.py    (CRUD, versioning, ref checks)  │
            │               │              │                                      │
            │               │              └─ indexer.py (resource → index rows)  │
            │               │                     ▲                               │
            │               └─ registry.py ───────┘  (types + search params)      │
            │                                                                     │
            │   SQLAlchemy Core (sync) ──► SQLite file /data/leanfhir.db          │
            └───────────────────────────────────┬─────────────────────────────────┘
                                                └──► or PostgreSQL (optional compose profile)
```

One process, one generic code path for all six resource types. Behavioural differences between types live only in the registry.

### 4.2 Technology choices

| Concern | Choice | Rationale / trade-off |
|---|---|---|
| Language | Python 3.12 | Stated preference. |
| Web | FastAPI + Uvicorn, **sync** endpoints | Sync handlers run in the threadpool; avoids async DB drivers. Adequate for the N3 envelope. |
| FHIR models | `fhir.resources` (Pydantic v2), `R4B` sub-package | Structural validation without hand-written models. See 5.3 for the R4 vs R4B caveat. |
| DB access | SQLAlchemy 2.x Core, `create_all()` at startup | One code path for SQLite and PostgreSQL. No ORM entities, no Alembic. |
| DB | SQLite (default), PostgreSQL 16 (optional) | SQLite: zero ops, single writer. PostgreSQL: concurrency, at the cost of a second container (often already present alongside EHRbase). |
| Storage model | Resource as JSON document + separate search-index table | Schema-free per resource type; portable search. Trade-off: index maintained in app code, writes touch two tables. |
| Tests | pytest + FastAPI TestClient (httpx) | |
| Packaging | `pyproject.toml`, pip, Docker, Compose | |

### 4.3 Data model

Two tables. Timestamps stored as UTC ISO-8601 strings (fixed width, millisecond precision, `Z` suffix) so that lexical comparison equals chronological comparison on both databases.

**`resource`**

| Column | Type | Notes |
|---|---|---|
| `resource_type` | varchar(32) | PK part |
| `id` | varchar(64) | PK part |
| `version_id` | integer | starts at 1 |
| `last_updated` | varchar(24) | UTC ISO-8601 |
| `content` | SQLAlchemy `JSON` | full resource including `id` and `meta` |

Index: (`resource_type`, `last_updated`).

**`search_index`** — one row per indexed value

| Column | Type | Used by |
|---|---|---|
| `resource_type` | varchar(32) | all |
| `resource_id` | varchar(64) | all |
| `param` | varchar(64) | all — search parameter name |
| `value_string` | text | string — lower-cased value |
| `value_exact` | text | string `:exact` — original value |
| `token_system` | text, nullable | token |
| `token_code` | text | token |
| `date_start` | varchar(24), nullable | date — inclusive lower bound |
| `date_end` | varchar(24), nullable | date — inclusive upper bound |
| `reference` | text | reference — normalised `{Type}/{id}` |

Indexes: (`resource_type`, `param`, `token_code`), (`resource_type`, `param`, `value_string`), (`resource_type`, `param`, `reference`), (`resource_type`, `param`, `date_start`), (`reference`) for inbound-reference checks, (`resource_type`, `resource_id`) for re-indexing. Foreign key to `resource` with `ON DELETE CASCADE` (enable `PRAGMA foreign_keys=ON` for SQLite).

`_id` and `_lastUpdated` are answered from the `resource` table directly, not indexed.

### 4.4 Request flows

**Write (create/update)**
1. Parse JSON → check `resourceType`/id rules (400).
2. Validate with the `fhir.resources` model for the type (422).
3. Begin transaction. For update: load current row; check `If-Match` (412). For conditional create: run the search (200/412).
4. Extract index rows via `indexer`; if `ENFORCE_REFERENCES`, check local references exist (422).
5. Set `id`, `meta.versionId`, `meta.lastUpdated`; upsert `resource`; replace `search_index` rows for this resource.
6. Commit. Return resource with `ETag`, `Location`.

Store the **original validated JSON** (as received, plus server-set `id`/`meta`), not a re-serialised model dump, to avoid lossy round-tripping of extensions and primitive extensions.

**Search**
1. Parse query string (or form body) → list of (param, modifier, [values]); validate against registry (400).
2. Build SQL: `SELECT … FROM resource r WHERE r.resource_type = :t AND EXISTS (SELECT 1 FROM search_index s WHERE s.resource_type = r.resource_type AND s.resource_id = r.id AND s.param = :p AND (<OR of value predicates>))` — one `EXISTS` per AND-ed parameter.
3. Run count query and page query (`ORDER BY`, `LIMIT`, `OFFSET`).
4. Assemble `Bundle` with links built from `BASE_URL` (or request URL if unset).

### 4.5 Project layout

```
leanfhir/
├── app/
│   ├── main.py        # app factory, lifespan (create_all, SQLite pragmas), exception handlers, /health, logging middleware
│   ├── config.py      # env settings (pydantic-settings)
│   ├── db.py          # engine, table definitions, session/transaction helper
│   ├── registry.py    # resource types, model classes, search parameter definitions
│   ├── indexer.py     # path walker + per-datatype extractors → index rows
│   ├── search.py      # query parsing, value parsing (token/string/date/reference), SQL building
│   ├── store.py       # read/create/update/delete, versioning, reference checks
│   ├── routes.py      # generic FHIR router, CapabilityStatement, require_auth no-op
│   ├── errors.py      # FhirError exception + OperationOutcome builder
│   └── cli.py         # `python -m app.cli reindex`
├── tests/
│   ├── conftest.py    # client fixture; DB URL from env so the suite can run on PostgreSQL
│   ├── fixtures/      # minimal + rich example JSON per resource type
│   └── test_*.py      # crud, validation, search_{token,string,date,reference}, paging, conditional, references, metadata
├── Dockerfile
├── docker-compose.yml # service `leanfhir`; profile `postgres` adds `db` and overrides DATABASE_URL
├── pyproject.toml
├── README.md
└── SPEC.md            # this document
```

---

## 5. Key design details

### 5.1 Generic router

Routes are declared once with `{resource_type}` as a path parameter and validated against the registry (404 if unknown). Do not generate per-type routers or per-type modules.

### 5.2 Registry (single source of truth)

```python
# registry.py — illustrative shape
@dataclass(frozen=True)
class SearchParam:
    name: str
    type: Literal["token", "string", "date", "reference"]
    paths: tuple[str, ...]            # dotted paths; arrays are flattened implicitly
    target: str | None = None         # reference type filter, e.g. "Patient" for Encounter.patient

REGISTRY: dict[str, ResourceDef] = {
    "Patient": ResourceDef(
        model=Patient,
        params=(
            SearchParam("identifier", "token", ("identifier",)),
            SearchParam("name", "string", ("name.family", "name.given", "name.text", "name.prefix", "name.suffix")),
            SearchParam("birthdate", "date", ("birthDate",)),
            SearchParam("organization", "reference", ("managingOrganization",)),
            ...
        ),
    ),
    ...
}
```

The registry drives indexing, search validation, reference checks and the `CapabilityStatement`.

### 5.3 Validation and the R4 / R4B note

`fhir.resources` ≥ 7 ships R5 (default), R4B and STU3 models; R4 (4.0.1) models exist only in the older Pydantic-v1 line (6.x), which is not a sound basis alongside current FastAPI. Decision:

- Validate with `fhir.resources.R4B` models.
- Declare `fhirVersion: "4.0.1"` in the `CapabilityStatement`.

Rationale: R4B's changes relative to R4 are concentrated in other resource areas (medication definition, evidence, citation, subscriptions); the six resources in scope are expected to be structurally the same. **Build task:** confirm this against the official R4B diff (http://hl7.org/fhir/R4B/diff.html) for the six types and record the result in the README. If a difference is found, document it as a known deviation rather than changing libraries.

### 5.4 Indexer

- A ~30-line dotted-path walker over the resource dict (flatten lists at every step). **No FHIRPath engine.**
- Extractors are chosen by the **shape of the value found**, per parameter type:
  - token: `Identifier` → (system, value); `CodeableConcept` → one row per `coding`; `Coding` → (system, code); `ContactPoint` → (system, value); plain string (code) → (None, value); bool → (None, "true"/"false").
  - string: plain string or each element of a string list.
  - date: `date`/`dateTime` string → range by precision; `Period` → (start lower bound, end upper bound), open ends as NULL.
  - reference: `Reference.reference` normalised to `{Type}/{id}`; skip if absent (logical reference).
- `patient`-style params use `target` to keep only references of that type.

### 5.5 SQLite specifics

On connect: `PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000; PRAGMA synchronous=NORMAL`. Run Uvicorn with one worker when `DATABASE_URL` is SQLite.

### 5.6 Error handling

A single `FhirError(status, issue_code, diagnostics)` exception and one handler producing `OperationOutcome`. Handlers also for Pydantic `ValidationError` (422, one issue per error), FastAPI/Starlette HTTP errors (404/405), and a catch-all (500). Issue codes: `invalid`, `structure`, `not-found`, `not-supported`, `conflict`, `duplicate`, `processing`, `exception`.

---

## 6. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:////data/leanfhir.db` | SQLAlchemy URL. PostgreSQL: `postgresql+psycopg://user:pass@db:5432/leanfhir` |
| `BASE_URL` | *(unset)* | Public base URL used in `Location`, `fullUrl`, paging links, reference normalisation. If unset, derived from the request. |
| `DEFAULT_COUNT` | `20` | Default page size |
| `MAX_COUNT` | `200` | Maximum page size |
| `ENFORCE_REFERENCES` | `true` | Referential integrity checks (F9) |
| `CORS_ORIGINS` | *(empty)* | Comma-separated origins; empty = CORS off |
| `LOG_LEVEL` | `INFO` | |
| `PORT` | `8000` | Container listen port |

**Compose**: default profile runs `leanfhir` only with volume `leanfhir-data:/data`. `docker compose --profile postgres up` adds `postgres:16-alpine` and sets `DATABASE_URL` accordingly. Container healthcheck calls `/health`.

**Runtime dependencies** (pin exact versions at build time): `fastapi`, `uvicorn`, `sqlalchemy`, `fhir.resources`, `pydantic-settings`, `psycopg[binary]`. Dev: `pytest`, `httpx`, `pytest-cov`, `ruff`.

---

## 7. Known deviations from the FHIR specification (to be listed in README)

1. Subset server: six resource types, limited interactions and search parameters.
2. Unknown search parameters are rejected (400) rather than ignored.
3. Paging uses a non-standard `_offset` parameter (clients should follow `Bundle.link`, which is standard).
4. No version history; delete is a hard delete, so a deleted id returns 404 rather than 410.
5. Validation uses R4B structural models while declaring R4 (5.3).
6. String search does not do accent/diacritic folding.
7. `Prefer` header is ignored.

---

## 8. openEHR linkage pattern (informative — goes in README)

LeanFHIR does not call the openEHR CDR. Recommended app-side pattern:

**Patient ↔ EHR** — the EHR points at the Patient (single direction, no dual-write):

```http
POST {ehrbase}/rest/openehr/v1/ehr
Content-Type: application/json

{
  "_type": "EHR_STATUS",
  "archetype_node_id": "openEHR-EHR-EHR_STATUS.generic.v1",
  "name": { "value": "EHR Status" },
  "subject": {
    "external_ref": {
      "id": { "_type": "GENERIC_ID", "value": "<Patient.id>", "scheme": "fhir-logical-id" },
      "namespace": "<leanfhir namespace, e.g. leanfhir-dev>",
      "type": "PERSON"
    }
  },
  "is_queryable": true,
  "is_modifiable": true
}
```

Lookup of the EHR for a patient: `GET {ehrbase}/rest/openehr/v1/ehr?subject_id=<Patient.id>&subject_namespace=<namespace>`.

Use the FHIR logical id (not a business identifier such as an NHI/MRN) as the link, because it is unique and immutable within this server; business identifiers stay in `Patient.identifier` and are searchable there. `scheme` and `namespace` values are project conventions — fix them once per deployment.

**Other links** (app conventions, all by FHIR logical id as `PARTY_REF`/identifier):

| openEHR | FHIR |
|---|---|
| `COMPOSITION.composer` (`PARTY_IDENTIFIED.external_ref`) | `Practitioner/{id}` |
| `EVENT_CONTEXT.health_care_facility` | `Organization/{id}` |
| `EVENT_CONTEXT.location` / participations | `Location/{id}` / `Practitioner/{id}` |
| Encounter link: an identifier element in the template's context (`other_context`) or `FEEDER_AUDIT` item id | `Encounter/{id}` |

Typical sequence: conditional-create Patient by identifier (F3) → create EHR with `external_ref` → create Encounter → commit compositions carrying the Encounter id.

---

## 9. Build plan and acceptance criteria

Build in order; each milestone ends with its tests passing on SQLite.

### M1 — Skeleton
- Project layout, config, DB tables via `create_all`, SQLite pragmas, logging middleware, error handling, Dockerfile, compose.
- **Accept:** `docker compose up` → `GET /health` 200; `GET /fhir/metadata` returns a valid `CapabilityStatement` listing the six types; `GET /fhir/Observation/1` → 404 with `OperationOutcome`.

### M2 — CRUD, validation, versioning
- Registry (types + models), store, generic router for F1, F2, F4, F5; F10, F11.
- **Accept (per type, parameterised tests):**
  - POST valid → 201, `Location`, `ETag W/"1"`, server UUID, `meta` set; body `id` ignored.
  - GET → 200 identical content; unknown id → 404.
  - PUT existing → 200, version 2; PUT new id → 201; body id ≠ URL id → 400; bad id format → 400.
  - PUT with stale `If-Match` → 412; with current → 200.
  - Invalid structure (e.g. `gender: 5`, unknown element) → 422 with issue location; wrong `resourceType` → 400; malformed JSON → 400.
  - DELETE → 204; then GET → 404; DELETE again → 204.
  - Extensions and primitive extensions (`_birthDate`) round-trip unchanged.
  - XML requested → 406.

### M3 — Indexing and search
- Indexer, search parser, SQL builder, Bundle assembly, paging; F6; `reindex` CLI.
- **Accept:**
  - Every parameter in 2.4 has at least one positive and one negative test.
  - Token: `identifier=sys|val`, `=val`, `=|val`, `=sys|`; `gender=female`; `active=true`.
  - String: `family=smi` matches "Smith" and "SMITHSON"; `family:exact=Smith`; `name:contains=mit`.
  - Date: `birthdate=1980`, `birthdate=ge1980-01-01&birthdate=lt1990`, Encounter `date` against closed and open-ended periods.
  - Reference: `patient=Patient/{id}`, `patient={id}`, absolute URL under base; `subject` vs `patient` behave per `target`.
  - AND (repeat) and OR (comma) semantics; `_id`, `_lastUpdated`, `_sort`.
  - Paging: 45 patients, `_count=20` → 3 pages via `next` links, correct `total`, no duplicates/omissions; `_count=0` → total only; `_count=1000` capped at `MAX_COUNT`.
  - Unknown param / unsupported modifier → 400 naming the parameter.
  - `POST /fhir/Patient/_search` form-encoded gives same result as GET.
  - Update changes index (old value no longer matches); delete removes index rows; `reindex` on a populated DB leaves search results unchanged.

### M4 — Conditional create and referential integrity
- F3, F9.
- **Accept:**
  - `If-None-Exist: identifier=sys|val` → first call 201, second call 200 with same id and no new version; two pre-existing matches → 412.
  - Encounter with `subject` → non-existent Patient → 422; with `ENFORCE_REFERENCES=false` → 201.
  - Reference to unsupported type or external absolute URL is accepted.
  - DELETE Patient referenced by an Encounter → 409; after deleting the Encounter → 204.

### M5 — PostgreSQL parity, docs, polish
- Compose `postgres` profile; run the full suite with `DATABASE_URL` pointing at PostgreSQL; README per N13; R4B diff check (5.3); non-root container; ruff clean; coverage ≥ 85%.
- **Accept:** entire test suite green on both SQLite and PostgreSQL; image size and startup within N2; README complete including sections 7 and 8.

### Instructions for the implementing agent
- Implement exactly this scope; section 1.5 lists what not to build. Where the spec is silent, choose the simplest behaviour consistent with FHIR R4 and note it in the README under "Known deviations" or "Implementation notes".
- Write tests alongside each milestone, not at the end.
- Do not add dependencies beyond section 6 without a stated reason.
- Keep one generic code path; no per-resource modules.

---

## 10. Future extensions (not in v0.1; listed only to show the seams suffice)

| Extension | Where it plugs in |
|---|---|
| API key / OIDC JWT auth | `require_auth` dependency (N8a) |
| More resource types (RelatedPerson, EpisodeOfCare, Appointment) | Registry entries (N8b) |
| `_include` for Encounter → Patient | `search.py` post-processing using stored references |
| Transaction Bundle for seeding | New route reusing `store` within one transaction |
| History / vread | Add `resource_history` table written in `store` |

## References

- FHIR R4 RESTful API: https://hl7.org/fhir/R4/http.html
- FHIR R4 Search: https://hl7.org/fhir/R4/search.html
- FHIR R4B differences: http://hl7.org/fhir/R4B/diff.html
- fhir.resources: https://github.com/nazrulworld/fhir.resources
- openEHR REST API (EHR): https://specifications.openehr.org/releases/ITS-REST/latest/ehr.html
