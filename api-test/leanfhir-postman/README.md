# LeanFHIR — Postman smoke-test collection

The Postman (Collection v2.1) version of the Bruno collection in `../leanfhir-bruno`, with the same
folders, requests, checks and run order. It is a smoke test of the LeanFHIR server (SPEC v0.1), not
the section 9 acceptance matrix, which stays in pytest. Synthetic data only.

| File | What |
|---|---|
| `LeanFHIR.postman_collection.json` | The collection (27 requests, 83 tests) |
| `LeanFHIR-local.postman_environment.json` | Environment `LeanFHIR local` (`baseUrl`, `healthUrl`) |

## Run in the Postman desktop app on Windows (server in WSL2)

1. Start the server in WSL2: `docker compose up -d` from the repo root.
2. In Postman: **Import**, then select both JSON files. From Windows the folder is:
   `\\wsl.localhost\<distro>\<path-to-repo>\api-test\leanfhir-postman`, e.g.
   `\\wsl.localhost\Ubuntu\home\<user>\lean-fhir-server\api-test\leanfhir-postman`
   (older Windows builds: `\\wsl$\Ubuntu\...`).
3. Select the environment **LeanFHIR local** (top right).
4. Right-click the collection, choose **Run collection**, and keep the folder order. To keep the
   test data for inspection, untick `99-cleanup`.

Notes:

- The address is `http://127.0.0.1:8000`, which WSL2 forwards from Windows. If it does not respond
  (e.g. `localhostForwarding=false` in `%UserProfile%\.wslconfig`), set `baseUrl`/`healthUrl` to the
  WSL address from `hostname -I`.
- The collection also defines `baseUrl`/`healthUrl` as collection variables with the same values,
  so it works without selecting the environment. An environment value overrides them.
- The Postman *web* app can only reach `127.0.0.1` through the Postman Desktop Agent. Use the
  desktop app.
- Import **copies** the collection into your Postman workspace; it does not link to these files.
  After changing it in Postman, export it again (Collection v2.1) over
  `LeanFHIR.postman_collection.json` to update the repo.

## Run with Newman (CLI)

```bash
cd api-test/leanfhir-postman
npx newman run LeanFHIR.postman_collection.json -e LeanFHIR-local.postman_environment.json

# create and test, but leave the data in the database
npx newman run LeanFHIR.postman_collection.json -e LeanFHIR-local.postman_environment.json \
  --folder 00-meta --folder 01-create --folder 02-read-update --folder 03-search --folder 04-conditional-errors
```

## How it works

- `Create Organization` sets a new `runId` (epoch ms) in its pre-request script. All test
  identifiers include it (`MRN-<runId>`, system `urn:leanfhir:test:*`), so runs do not interfere.
- Created ids are stored as **collection variables** (`orgId`, `patientId`, ...). Requests in
  `02` to `99` need `01-create` to have run first in the same session.
- The 422 and 409 checks need `ENFORCE_REFERENCES=true` (the default).
- `meta.versionId` is a JSON string in FHIR, so tests compare it with `"1"`, not `1`.
