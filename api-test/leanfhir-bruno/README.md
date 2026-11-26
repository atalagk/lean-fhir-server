# LeanFHIR — Bruno smoke-test collection

Minimal end-to-end check of the LeanFHIR server (SPEC v0.1). Synthetic data only.
It is a smoke test, not the section 9 acceptance matrix (that stays in pytest).

## Run

### Bruno app on Windows, server in WSL2

1. Start the server in WSL2: `docker compose up -d` from the repo root.
2. In the Windows Bruno app: **Open Collection** and enter this folder's WSL path:
   `\\wsl.localhost\Ubuntu\home\koray\projects\lean-fhir-server\api-test\leanfhir-bruno`
   (older Windows builds: `\\wsl$\Ubuntu\...`).
3. Select the environment `local` (top right), then run the collection with the Runner, or run
   requests one by one in folder order.

Notes for this setup:

- `local` uses `http://127.0.0.1:8000`. WSL2 forwards ports from Windows to WSL, so Windows can
  reach the server there. Avoid `localhost`: Bruno runs on Node, which on Windows may try IPv6
  `::1` first and fail to connect.
- If that address does not respond (for example when `localhostForwarding=false` in
  `%UserProfile%\.wslconfig`), use the WSL address from `hostname -I` in WSL instead.
- Files opened over `\\wsl.localhost` are saved straight into the repo. Bruno may not notice
  changes made on the WSL side while the collection is open; close and reopen it to refresh.

### CLI

With `@usebruno/cli` installed (`npm i -g @usebruno/cli`), run from this folder:

```bash
# create, test, and LEAVE the data in the database for inspection
bru run 00-meta 01-create 02-read-update 03-search 04-conditional-errors --env local

# everything including deletes (database ends as it started)
bru run -r --env local
```

To use another server, change `environments/local.bru` or pass
`--env-var baseUrl=http://host:port/fhir --env-var healthUrl=http://host:port/health`.

## Folders

| Folder | Covers (SPEC ref) |
|---|---|
| `00-meta` | health (F8), CapabilityStatement (F7) |
| `01-create` | create for all six types (F2, F11), in reference order |
| `02-read-update` | read (F1), update with `If-Match` current and stale (F4) |
| `03-search` | token, string, date, reference search; unknown parameter → 400 (F6) |
| `04-conditional-errors` | conditional create (F3), unresolved reference → 422 (F9), invalid structure → 422 (F10), delete blocked → 409 (F9) |
| `99-cleanup` | delete in reverse reference order (F5), then read → 404 |

## Notes

- Each run creates six new resources. Identifiers carry a per-run id
  (`MRN-<epoch ms>`, set in `01-create/01-organization.bru`), so runs do not interfere.
- Ids are passed between requests as runtime variables. `99-cleanup` therefore only
  works in the same CLI run, or the same Bruno app session, as `01-create`.
  To clear data left by earlier runs, reset the database.
- Requests in `02` to `99` depend on `01-create` having run first.
- Requires `ENFORCE_REFERENCES=true` (the default) for the 422 and 409 checks.
- `meta.versionId` is a JSON string in FHIR, so the assertions quote it (`eq "1"`). Bruno turns an
  unquoted `eq 1` into a number, and the comparison fails.

## Looking at the data in the database

Tables: `resource` and `search_index` (SPEC 4.3). Test data is recognisable by
identifier systems starting with `urn:leanfhir:test:`.

```sql
SELECT resource_type, id, version_id, last_updated FROM resource ORDER BY last_updated;
SELECT param, value_string, token_system, token_code, date_start, date_end, reference
  FROM search_index WHERE resource_id = '<id>';
```

SQLite runs in WAL mode, so a plain copy of `leanfhir.db` can miss recent writes;
copy the `-wal` and `-shm` files with it, or query the file in place.
