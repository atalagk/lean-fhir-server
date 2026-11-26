# LeanFHIR

Lean FHIR R4 demographic/admin server (Patient, Practitioner, PractitionerRole, Organization, Location, Encounter) for PoC/MVP use alongside openEHR apps. Python 3.12, FastAPI (sync), SQLAlchemy Core, SQLite default / PostgreSQL optional, Docker.

## Source of truth

`spec/SPEC.md` is authoritative for scope, behaviour, architecture and acceptance criteria. Read the relevant section before implementing or changing anything. Do not restate spec content here.

- Section 1.5: non-goals. Do not build anything listed there, and do not add features the spec does not ask for.
- Section 9: milestones M1–M5 and their acceptance criteria.
- If the spec is silent or ambiguous: choose the simplest FHIR R4-consistent behaviour and record it in README under "Implementation notes". If the spec looks wrong, stop and ask rather than deviating silently.
- If a decision changes, update `spec/SPEC.md` in the same change.

## Workflow

- Work one milestone at a time, in order. Do not start the next until the current one's acceptance criteria pass and I confirm.
- Write tests with the code for each milestone, not afterwards. Each acceptance criterion maps to at least one test.
- At the end of a milestone, report: criteria met, criteria not met, deviations from the spec, anything added to README notes.
- Run the tests and lint before reporting a milestone as done.

## Hard rules

- One generic code path for all resource types, driven by `app/registry.py`. No per-resource modules, routers or tables.
- All search goes through the `search_index` table. No dialect-specific SQL or JSON operators; behaviour must be identical on SQLite and PostgreSQL.
- No FHIRPath engine, no Alembic, no async DB drivers, no ORM entities.
- No new dependencies beyond SPEC section 6 without asking first.
- Store the resource JSON as received (plus server-set `id` and `meta`); never store a re-serialised model dump.
- Every non-2xx response body is an `OperationOutcome`.
- Keep it small: target about 1,500 lines of application code. Prefer deleting to abstracting.

## Commands

Keep this section current once the project skeleton exists (M1).

All local Python work runs inside the project venv at `.venv/` (git-ignored). Never install into the system Python. The system Python is 3.10; Python 3.12 is provided by uv (`uv python install 3.12`, shim at `~/.local/bin/python3.12`).

```bash
python3.12 -m venv .venv                # once
source .venv/bin/activate               # every shell
pip install -e ".[dev]"                 # local install
DATABASE_URL=sqlite:///./leanfhir.db uvicorn app.main:app --reload   # run locally (SQLite)
pytest                                  # tests (temp SQLite)
DATABASE_URL=postgresql+psycopg://leanfhir:leanfhir@localhost:5432/leanfhir pytest   # tests on PostgreSQL
pytest --cov=app --cov-report=term-missing
ruff check . && ruff format .
docker compose up --build               # container, SQLite
docker compose --profile postgres up --build
python -m app.cli reindex               # rebuild search index
```

## Conventions

- Type hints throughout; ruff for lint and format.
- Config only through environment variables (`app/config.py`).
- Test fixtures are synthetic data only. No real patient data anywhere in the repo.