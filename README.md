# LeanFHIR

Lean FHIR R4 demographic/admin server (Patient, Practitioner, PractitionerRole, Organization,
Location, Encounter) for PoC/MVP use alongside openEHR apps. See `spec/SPEC.md`.

> **Security warning:** no authentication. Run only on a trusted network or behind a gateway.
> Synthetic or de-identified data only — never real patient data.

## Quick start

```bash
docker compose up --build
curl http://localhost:8000/health
curl http://localhost:8000/fhir/metadata
```

Local development (Python 3.12):

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
DATABASE_URL=sqlite:///./leanfhir.db uvicorn app.main:app --reload
pytest
```

The default `DATABASE_URL` (`sqlite:////data/leanfhir.db`) targets the container volume; override
it when running outside Docker.

## Implementation notes

- The `CapabilityStatement` is generated from `app/registry.py` and advertises `versioning:
  versioned-update` (versionId maintained, `If-Match` supported, no vread/history).
- `/health` failure returns 503 with an `OperationOutcome` body (every non-2xx body is an
  `OperationOutcome`).
- Uvicorn's own access log is disabled; the app emits one JSON log line per request to stdout.
- Worker count: the container runs one Uvicorn worker. For PostgreSQL deployments, set
  `WEB_CONCURRENCY` (read natively by Uvicorn) to run more. Never use more than one with SQLite.
- Request bodies must be `application/fhir+json` or `application/json` (or have no Content-Type);
  any other Content-Type gets 415.
- Format negotiation: a `_format` value other than `json`/`application/json`/`application/fhir+json`,
  or an `Accept` header that allows no JSON type (or `*/*`), gets 406.
- `If-Match` takes `W/"n"` or `"n"`. A malformed value gets 400. An `If-Match` on a resource that
  does not exist gets 412.
- A concurrent write detected at commit time (two writers updating the same version) gets 409.
- Stored JSON is the body as received, except that `resourceType`, `id` and `meta` are moved to
  the front. `meta.versionId`/`meta.lastUpdated` are server-set; other `meta` content is kept.
- `Location` is `{base}/{type}/{id}` (no `_history` segment, since there is no history).
- Delete is a hard delete, so a later PUT to the same id starts again at `versionId` "1".
- Unknown resource types are rejected by route matching (404). Known paths with an unsupported
  method get 405.
- Search parameter values:
  - An empty value (`family=`) gets 400. FHIR would ignore it, but this server never ignores a filter silently.
  - Repeating a control parameter (`_count`, `_offset`, `_sort`, `_format`) gets 400.
  - `_count` above `MAX_COUNT` is reduced to `MAX_COUNT`, and the paging links show the count actually used.
  - FHIR escaping in values (`\,`, `\|`, `\\`) is supported.
- Date search:
  - A value without a timezone is treated as UTC.
  - Fractional seconds are cut to milliseconds.
  - A Period with an open end never matches `eq`, because it isn't fully inside any range. It does
    match `ge`/`gt`/`le`/`lt` on its open side.
- Reference search:
  - A bare id (`subject=123`) matches `{Type}/123` for any of the six supported types, or only the
    target type for parameters like `patient`.
  - References in resources to external absolute URLs or `urn:` ids are indexed as written and can
    only be found by that exact value.
  - Contained references (`#id`) are not indexed.
- Token search: a CodeableConcept that has only `text` (no `coding`) is not indexed.
- `POST /fhir/{type}/_search` needs an `application/x-www-form-urlencoded` body (other types get 415).
  Parameters in the URL query and in the body are combined.
- Paging links always include `_count` and `_offset`.
- Reference normalisation needs the server base URL. When `BASE_URL` is unset, writes use the URL
  of the incoming request, but `python -m app.cli reindex` has no request, so it cannot normalise
  absolute references back to this server. **Set `BASE_URL` in any shared deployment.**
- Conditional create (`If-None-Exist`):
  - One match returns 200 with the existing resource, `ETag` and `Last-Modified`, and no `Location`.
  - More than one match gets 412 with issue code `duplicate`.
  - The query goes through the normal search parser (unknown parameters → 400). A leading `?` is
    allowed, an empty header gets 400, and paging parameters are ignored.
- Referential integrity:
  - An unresolved reference gets 422 with issue code `processing`, listing every unresolved target.
  - A delete blocked by inbound references gets 409, naming up to five referring resources.
  - A resource may reference itself; that neither fails the check nor blocks its own deletion.
  - `ENFORCE_REFERENCES=false` turns off both the write check and the delete check.
- Concurrency: the conditional-create search and the reference checks run inside the write
  transaction, but concurrent requests are not serialised. Two simultaneous conditional creates
  can both create, and a delete racing a create can leave a dangling reference. This is accepted
  for the PoC scale envelope (N3).
