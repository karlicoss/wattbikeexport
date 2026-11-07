# Wattbike Hub exporter

This repository follows the export/DAL split described in
[Building data liberation infrastructure](https://beepb00p.xyz/exports.html):

- the **export layer** communicates with Wattbike and preserves raw data;
- the **data access layer (DAL)** works offline and interprets exported data.

The implementation lives under `src/wattbikeexport`:

```text
src/wattbikeexport/
  _http.py    HTTP retries and resumable downloads
  common.py   shared raw archive primitives
  export.py   authentication, API access, and raw export
  dal.py      offline access and typed Wattbike bindings
```

## Exporting

Create `secrets.json`:

```json
{
  "email": "name@example.com",
  "password": "your Wattbike password"
}
```

Run the exporter:

```console
uv run --extra export -m wattbikeexport.export \
  --secrets secrets.json \
  --output exports/2026-06-20
```

The `export` extra contains the export layer's dependencies, `requests` and `kloggy` for logging.
The DAL only needs hpikit, which has no required dependencies of its own.
Its `optional` extra makes parsing faster with msgspec.

Useful options:

```console
uv run --extra export -m wattbikeexport.export \
  --secrets secrets.json \
  --output exports/2026-06-20 \
  --after 2026-01-01
uv run --extra export -m wattbikeexport.export \
  --secrets secrets.json \
  --output exports/2026-06-20 \
  --before 2026-01-01
uv run --extra export -m wattbikeexport.export \
  --secrets secrets.json \
  --output exports/2026-06-20
```

`--after` is inclusive and `--before` is exclusive.
Dates and timestamps use ISO 8601 and are converted to UTC.

For incremental updates, reuse the same output directory.
Existing session directories are skipped without changing their files or metadata.

The exporter uses the current Hub at `v2.hub.wattbike.com`.
It authenticates with OAuth authorization code + PKCE and reads workouts and assets from `v2.api.wattbike.com/v1`.
The same email and password work after Wattbike's account migration.

## Raw archive

The exporter writes:

```text
wattbike-export/
  profile.json
  workouts.json
  sessions/
    <timestamp>_<session-id>/
      metadata.json
      <asset-id>.<format>
```

Each session's `metadata.json` preserves the raw API response from its first successful export.
The root `profile.json` and `workouts.json` files refresh on each successful run.
The DAL reads the per-session metadata, so later server-side edits do not change archived sessions.

`profile.json` is the single current profile, preserving the raw response from the current Hub API.
Old profiles have a top-level `objectId`; new profiles have `user.id` and link to the old account through `user.wbParseId`.
When first replacing an old profile, the exporter preserves its exact bytes in `legacy/profile.json`.
That historical copy is never refreshed or used by the DAL.

Files currently advertised by Wattbike include FIT, TCX, WBS, and WBSR.
Asset references determine the downloaded formats.

An archive from the old API also has `profile-objects.json`, `sessions.json`, and per-session `metadata.json`.
These remain intact when the new exporter runs in the same directory.
A workout whose `legacyId` matches an archived `objectId` is skipped.
New API workouts are skipped by their UUID once archived.
The DAL distinguishes the old and new `metadata.json` schemas by their `objectId` or `id` fields.

The export layer deliberately does not generate normalized revolution data.
WBS remains the authoritative raw source, and transformations belong in the DAL.
New sessions download into `.partial/<session-directory>/` outside the completed `sessions/` tree.
Interrupted downloads remain as `.part` files and resume with an HTTP range request.
Once all files and `metadata.json` are written, the complete directory is moved into `sessions/`.
Workouts that the API has not marked complete are deferred to a later run.

## Offline DAL

Inspect and verify an archive without `requests` or network access:

```console
uv run -m wattbikeexport.dal --source wattbike-export --verify
```

Programmatic access from the repository root:

```python
from wattbikeexport.dal import DAL

dal = DAL("wattbike-export")

for session in dal.sessions():
    print(session.start_time, session.title, session.summary)

    for revolution in session.revolutions():
        print(revolution.power, revolution.cadence, revolution.pes)
```

## Verification

```console
uv run --extra export -m wattbikeexport.export \
  --secrets secrets.json \
  --output exports/2026-06-20 \
  --help
uv run -m wattbikeexport.dal --source wattbike-export --verify
.ci/run
```

Run an individual check through tox:

```console
uv tool run --with tox-uv tox run -e tests
uv tool run --with tox-uv tox run -e ruff
uv tool run --with tox-uv tox run -e mypy
uv tool run --with tox-uv tox run -e ty
```

The API and authentication flow are undocumented and may change. See
[docs/research.md](docs/research.md) for research and protocol details, and
[docs/data_notes.md](docs/data_notes.md) for observed data quirks.
