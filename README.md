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
