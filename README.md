# LeanFHIR

A lean FHIR R4 server for demographic and administrative data (Patient, Practitioner,
PractitionerRole, Organization, Location, Encounter). It is meant for PoC/MVP projects that keep
demographics in FHIR alongside an openEHR clinical data repository. It is a single small Python
service with SQLite storage that runs in one Docker container.

The authoritative specification is [`spec/SPEC.md`](spec/SPEC.md).

> ## ⚠️ Security warning
>
> **There is no authentication or authorisation.** Anyone who can reach the port can read, change
> and delete every record. Run LeanFHIR only on a trusted network or behind a gateway that does
> authentication. **Use synthetic or de-identified data only. Never use real patient data.**

---

## Quick start

```bash
docker compose up --build
curl http://localhost:8000/health          # {"status":"ok"}
curl http://localhost:8000/fhir/metadata   # CapabilityStatement
```

Data is kept in the SQLite file `/data/leanfhir.db` on the named volume `leanfhir-data`. The
container runs as a non-root user and has a healthcheck on `/health`.

### Local development (Python 3.12)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
DATABASE_URL=sqlite:///./leanfhir.db uvicorn app.main:app --reload
```

The default `DATABASE_URL` points at the container path `/data`, so override it when running
outside Docker.

### Tests and lint

```bash
pytest                                     # temp SQLite per test
pytest --cov=app --cov-report=term-missing
ruff check . && ruff format --check .

# the same suite inside the container
docker build --target test -t leanfhir:test . && docker run --rm leanfhir:test
```

## Configuration

Configuration is through environment variables only.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:////data/leanfhir.db` | SQLAlchemy URL |
| `BASE_URL` | *(unset)* | Public base URL, e.g. `https://fhir.example.org/fhir`. Used in `Location`, `fullUrl`, paging links and reference normalisation. If unset, it is derived from each request. **Set it in any shared deployment** (see notes). |
| `DEFAULT_COUNT` | `20` | Default search page size |
| `MAX_COUNT` | `200` | Maximum search page size |
| `ENFORCE_REFERENCES` | `true` | Referential integrity checks on write and delete |
| `CORS_ORIGINS` | *(empty)* | Comma-separated allowed origins; empty means CORS is off |
| `LOG_LEVEL` | `INFO` | Log level of the JSON request log |
| `PORT` | `8000` | Listen port inside the container |

With Docker Compose, `CORS_ORIGINS` and `BASE_URL` are passed through from the shell or from a
`.env` file next to `docker-compose.yml` (git-ignored). For example, to use the Hoppscotch web app:

```bash
CORS_ORIGINS=https://hoppscotch.io docker compose up -d     # or put CORS_ORIGINS=... in .env
```

Allowed origins can use all methods and headers, and can read `ETag`, `Location` and
`Last-Modified`. Desktop clients (Postman, Bruno, the Hoppscotch desktop app) do not need CORS.

The container runs a single Uvicorn worker, which SQLite requires. PostgreSQL can be selected by
setting `DATABASE_URL` (`postgresql+psycopg://user:pass@host:5432/leanfhir`); the code is
dialect-neutral, but **PostgreSQL is untested in v0.1** and there is no compose profile for it.
With a non-SQLite database, `WEB_CONCURRENCY` (read by Uvicorn) sets the worker count.

## Supported interactions

All six resource types support the same interactions under the base path `/fhir`. Only JSON is
supported (`application/fhir+json`).

| Interaction | Request | Result |
|---|---|---|
| read | `GET /fhir/{type}/{id}` | 200 with `ETag: W/"n"` and `Last-Modified`; 404 if absent |
| create | `POST /fhir/{type}` | 201 with `Location` and `ETag`; server-assigned UUID (a body `id` is ignored) |
| conditional create | `POST /fhir/{type}` + `If-None-Exist: {query}` | 0 matches: create (201). 1 match: 200 with the existing resource. More than 1: 412 |
| update | `PUT /fhir/{type}/{id}` | 200, or 201 when creating. Body `id` must equal the URL id. Optional `If-Match: W/"n"`; a mismatch gets 412 |
| delete | `DELETE /fhir/{type}/{id}` | 204 (also when the resource is absent). 409 if other resources reference it |
| search | `GET /fhir/{type}?…` or `POST /fhir/{type}/_search` (form-encoded) | 200 with a `searchset` Bundle |
| capabilities | `GET /fhir/metadata` | `CapabilityStatement` generated from the registry |
| health | `GET /health` | `{"status":"ok"}`, or 503 when the database is unreachable |

Every error response body is an `OperationOutcome`. Status codes used: 400 (bad request or search
value), 404, 405, 406 (non-JSON format requested), 409, 412, 415 (non-JSON body), 422 (structural
validation failure or unresolved reference) and 500.

Writes are validated structurally against the FHIR model (no profile or terminology checks). The
server sets `meta.versionId` (starting at "1") and `meta.lastUpdated`. The body is otherwise
stored exactly as sent, so extensions and primitive extensions (e.g. `_birthDate`) round-trip
unchanged.

## Search

### Control parameters

| Parameter | Behaviour |
|---|---|
| `_count` | Page size; default 20, capped at `MAX_COUNT`. `_count=0` returns only the total |
| `_offset` | Zero-based offset; used by the `next`/`previous` links |
| `_sort` | `_lastUpdated` or `-_lastUpdated`. The default order is most recently updated first, then by id |
| `_id` | Logical id; comma-separated values give OR |
| `_lastUpdated` | Date search on the last update time |

- Repeating a parameter means AND; comma-separated values mean OR.
- **Unknown parameters and unsupported modifiers get 400**, with an `OperationOutcome` naming the
  parameter. They are never silently ignored.

### Matching by parameter type

| Type | Matching |
|---|---|
| token | `code`, `system\|code`, `\|code` (no system), `system\|` (any code in system). Case-sensitive exact match. Booleans are `true`/`false` |
| string | Case-insensitive starts-with. Modifiers: `:exact` (case-sensitive full match) and `:contains` |
| date | Prefixes `eq` (default), `ge`, `gt`, `le`, `lt`. Values are ranges by precision (`2024` = the whole year). Missing Period ends are open-ended. A value without a timezone means UTC |
| reference | `Type/id`, a bare `id`, or an absolute URL under the server base. No chaining |

### Parameters per resource

All types also support `_id` and `_lastUpdated`.

| Resource | Parameters |
|---|---|
| Patient | `identifier` (token), `name` (string), `family` (string), `given` (string), `birthdate` (date), `gender` (token), `active` (token), `telecom` (token), `organization` (reference), `general-practitioner` (reference) |
| Practitioner | `identifier` (token), `name` (string), `family` (string), `given` (string), `active` (token) |
| PractitionerRole | `identifier` (token), `practitioner` (reference), `organization` (reference), `location` (reference), `role` (token), `specialty` (token), `active` (token) |
| Organization | `identifier` (token), `name` (string, also matches `alias`), `type` (token), `active` (token), `partof` (reference) |
| Location | `identifier` (token), `name` (string, also matches `alias`), `status` (token), `type` (token), `organization` (reference), `partof` (reference) |
| Encounter | `identifier` (token), `subject` (reference), `patient` (reference, Patient subjects only), `status` (token), `class` (token), `type` (token), `date` (date, from `period`), `participant` (reference), `location` (reference), `service-provider` (reference), `part-of` (reference) |

## curl examples

```bash
B=http://localhost:8000/fhir
H='Content-Type: application/fhir+json'

# Create a Patient, or return the existing one with that identifier
curl -s -X POST $B/Patient -H "$H" -H 'If-None-Exist: identifier=urn:example:mrn|12345' -d '{
  "resourceType": "Patient",
  "identifier": [{"system": "urn:example:mrn", "value": "12345"}],
  "name": [{"family": "Example", "given": ["Pat"]}],
  "gender": "female", "birthDate": "1980-04-12"
}'

# Read, then update (PUT replaces the whole resource; send it in full with the id and version)
curl -s $B/Patient/<id>
curl -s -X PUT $B/Patient/<id> -H "$H" -H 'If-Match: W/"1"' -d '{
  "resourceType": "Patient", "id": "<id>", "active": true,
  "identifier": [{"system": "urn:example:mrn", "value": "12345"}],
  "name": [{"family": "Example", "given": ["Pat"]}],
  "gender": "female", "birthDate": "1980-04-12"
}'

# Create an Encounter for the Patient
curl -s -X POST $B/Encounter -H "$H" -d '{
  "resourceType": "Encounter", "status": "in-progress",
  "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
  "subject": {"reference": "Patient/<id>"},
  "period": {"start": "2024-03-01T09:00:00Z"}
}'

# Search
curl -s "$B/Patient?family=exa&birthdate=ge1980&birthdate=lt1990"
curl -s "$B/Patient?identifier=urn:example:mrn|12345"
curl -s "$B/Encounter?patient=<id>&date=ge2024-01-01&_sort=-_lastUpdated&_count=10"
curl -s -X POST $B/Patient/_search -d 'family=exa&gender=female'

# Delete (409 while an Encounter still references the Patient)
curl -s -X DELETE $B/Patient/<id>
```

## Operations

- **Rebuild the search index** from the stored resources:
  `docker compose exec leanfhir python -m app.cli reindex`.
- **Back up** by copying the SQLite file from the `leanfhir-data` volume, ideally while the server
  is stopped.
- **No schema migrations.** A new version may need a fresh database.
- **Logs** are JSON lines on stdout, one per request: method, path, status, duration, resource
  type and id. Request bodies are never logged.

## openEHR linkage pattern

LeanFHIR does not call the openEHR CDR (e.g. EHRbase); linking is the app's job. The recommended
app-side pattern follows.

**Patient ↔ EHR.** The EHR points at the Patient (one direction, no dual-write):

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

To look up the EHR for a patient:
`GET {ehrbase}/rest/openehr/v1/ehr?subject_id=<Patient.id>&subject_namespace=<namespace>`.

Use the FHIR logical id as the link, not a business identifier such as an NHI or MRN, because it
is unique and immutable within this server. Business identifiers stay in `Patient.identifier`,
where they can be searched. The `scheme` and `namespace` values are project conventions; fix them
once per deployment.

**Other links** (app conventions, all by FHIR logical id as `PARTY_REF`/identifier):

| openEHR | FHIR |
|---|---|
| `COMPOSITION.composer` (`PARTY_IDENTIFIED.external_ref`) | `Practitioner/{id}` |
| `EVENT_CONTEXT.health_care_facility` | `Organization/{id}` |
| `EVENT_CONTEXT.location` / participations | `Location/{id}` / `Practitioner/{id}` |
| Encounter links: repeating identifier in `EVENT_CONTEXT.other_context` (see below) | `Encounter/{id}` (0..*) |

**Composition ↔ Encounter (many-to-many).** A composition may be anchored to zero, one or several
encounters, and an encounter may have many compositions. The link is held on the openEHR side
only; LeanFHIR never references compositions.

- In each template that needs it, add to `context/other_context` a repeating (0..*) ELEMENT with a
  `DV_IDENTIFIER` value, one occurrence per linked Encounter:
  - `id` = `Encounter.id` (the FHIR logical id)
  - `type` = `Encounter`
  - `issuer` / `assigner` = the LeanFHIR namespace used for the Patient link (e.g. `leanfhir-dev`)
- Do not use `FEEDER_AUDIT.originating_system_item_ids` for this, because it means feeder-system
  provenance. Do not use `LOCATABLE.links` either, because `LINK.target` is a `DV_EHR_URI`, not an
  external URL.
- Where encounters form a hierarchy, `Encounter.partOf` can also be set on the FHIR side
  (searchable via `part-of`). It adds to the list on the composition; it does not replace it.

Resolve all encounters of a composition in one call (a comma means OR on `_id`):

```http
GET /fhir/Encounter?_id=<id1>,<id2>,<id3>
```

Find the compositions anchored to an encounter (AQL; the node ids are template-specific
placeholders):

```sql
SELECT c/uid/value
FROM EHR e CONTAINS COMPOSITION c
WHERE c/context/other_context[at0001]/items[at0002]/value/id = '<Encounter.id>'
```

The link is one-directional and LeanFHIR is standalone, so nothing keeps the two systems
consistent: deleting an Encounter in LeanFHIR does not touch compositions that cite it. The app
must handle ids that no longer resolve, i.e. ids missing from the `_id` search result.

Typical sequence: conditional-create the Patient by identifier → create the EHR with
`external_ref` → create the Encounter(s) → commit compositions carrying the list of Encounter ids.

## Known deviations from the FHIR specification

1. This is a subset server: six resource types, limited interactions and search parameters.
2. Unknown search parameters are rejected (400) instead of ignored.
3. Paging uses a non-standard `_offset` parameter. Clients should follow `Bundle.link`, which is
   standard.
4. There is no version history, and delete is a hard delete, so a deleted id returns 404 rather
   than 410.
5. Validation uses R4B structural models while the server declares R4 (`fhirVersion` 4.0.1). See
   the R4B check below.
6. String search does not fold accents or diacritics.
7. The `Prefer` header is ignored; create and update always return the full resource.

### R4B vs R4 check (SPEC 5.3)

Checked on 2026-11-27 against the official diff at <https://hl7.org/fhir/R4B/diff.html>:

- **Patient, Practitioner, PractitionerRole, Organization, Location, Encounter:** "No Changes".
  The datatypes they use (HumanName, Identifier, Reference, Period, CodeableConcept, Coding,
  ContactPoint, Address, Meta) are also unchanged.
- **Base datatype changes that apply:** `Element.id` changed from string to id, `Extension.url`
  changed from string to uri, and `Extension.value[x]` gained the types `CodeableReference` and
  `RatioRange`.
- **Effect:** the library (`fhir.resources` 8.3.0, R4B) does not enforce the tighter
  `Element.id` format, so element ids are accepted as in R4. It does accept
  `valueCodeableReference` and `valueRatioRange` in extensions, which strict R4 validation would
  reject. LeanFHIR is therefore slightly more permissive than R4 for these two extension value
  types. This is a known deviation; nothing else differs for the six resources.

## Implementation notes

Behaviour where the spec was silent or left a choice.

**Format and headers**
- Request bodies must be `application/fhir+json` or `application/json` (or have no Content-Type).
  Any other Content-Type gets 415.
- Format negotiation: a `_format` other than `json`, `application/json` or
  `application/fhir+json` gets 406. So does an `Accept` header that allows no JSON type (nor `*/*`).
- `If-Match` accepts `W/"n"` or `"n"`. A malformed value gets 400. `If-Match` on a resource that
  does not exist gets 412.
- `Location` is `{base}/{type}/{id}` (no `_history` segment, since there is no history).
- Unknown resource types are rejected by route matching (404). Known paths with an unsupported
  method get 405.

**Storage and versioning**
- Stored JSON is the body as received, except that `resourceType`, `id` and `meta` are moved to
  the front. `meta.versionId` and `meta.lastUpdated` are set by the server; other `meta` content
  is kept.
- Delete is a hard delete, so a later PUT to the same id starts again at `versionId` "1".
- If two writers update the same version at once, the second gets 409.
- The `CapabilityStatement` advertises `versioning: versioned-update`: the version id is
  maintained and `If-Match` is supported, but there is no vread or history.

**Search**
- An empty value (`family=`) gets 400. FHIR would ignore it, but this server never drops a filter
  silently.
- Repeating a control parameter (`_count`, `_offset`, `_sort`, `_format`) gets 400.
- `_count` above `MAX_COUNT` is reduced to `MAX_COUNT`, and the paging links show the count
  actually used. Paging links always include `_count` and `_offset`.
- FHIR escaping in values (`\,`, `\|`, `\\`) is supported.
- Dates:
  - A value without a timezone is UTC.
  - Fractional seconds are cut to milliseconds.
  - A Period with an open end never matches `eq`, because it is never fully inside a range. It
    does match `ge`/`gt`/`le`/`lt` on its open side.
- References:
  - A bare id (`subject=123`) matches `{Type}/123` for any of the six types, or only the target
    type for parameters such as `patient`.
  - References in resources to external absolute URLs or `urn:` ids are indexed as written and
    can only be found by that exact value.
  - Contained references (`#id`) are not indexed.
- Token search does not index a CodeableConcept that has only `text` (no `coding`).
- `POST /fhir/{type}/_search` needs an `application/x-www-form-urlencoded` body (other types get
  415). Parameters in the URL query and in the body are combined.

**Base URL**
- Reference normalisation needs the server base URL. When `BASE_URL` is unset, writes use the URL
  of the incoming request. `python -m app.cli reindex` has no request, so it cannot turn absolute
  references to this server back into `Type/id`. **Set `BASE_URL` in any shared deployment.**

**Conditional create and referential integrity**
- With `If-None-Exist`:
  - One match returns 200 with the existing resource, `ETag` and `Last-Modified`, and no
    `Location`.
  - More than one match gets 412 with issue code `duplicate`.
  - The query goes through the normal search parser. A leading `?` is allowed, an empty header
    gets 400, and paging parameters are ignored.
- An unresolved reference gets 422 with issue code `processing`, listing every unresolved target.
- A delete blocked by inbound references gets 409, naming up to five referring resources.
- A resource may reference itself; that neither fails the check nor blocks its own deletion.
- Only references covered by a reference search parameter are checked. For example,
  `Encounter.episodeOfCare` is not checked.
- `ENFORCE_REFERENCES=false` turns off both the write check and the delete check.
- Concurrency: the conditional-create search and the reference checks run inside the write
  transaction, but concurrent requests are not serialised. Two simultaneous conditional creates
  can both create, and a delete racing a create can leave a dangling reference. This is accepted
  for the PoC scale envelope.

**Operations**
- When `/health` fails it returns 503 with an `OperationOutcome` body.
- Uvicorn's own access log is off; the app writes one JSON log line per request instead.

## Not supported

Profile or terminology validation; `_history`/vread; patch; conditional update or delete;
transaction/batch Bundles; `$operations`; `_include`/`_revinclude`; chained search; `_has`; `:missing`;
`:not`; `_filter`; XML; GraphQL; Subscriptions; SMART on FHIR; authentication; any UI. See SPEC
section 1.5.
