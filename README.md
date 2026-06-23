# Wattbike Hub exporter

This repository follows the export/DAL split described in
[Building data liberation infrastructure](https://beepb00p.xyz/exports.html):

- the **export layer** communicates with Wattbike and preserves raw data;
- the **data access layer (DAL)** works offline and interprets exported data.

The implementation lives under `src/wattbikeexport`:

```text
src/wattbikeexport/
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
uv run --extra export -m wattbikeexport.export
```

The `export` extra contains the export layer's only dependency, `requests`.
The DAL has no third-party dependencies.

Useful options:

```console
uv run --extra export -m wattbikeexport.export --after 2026-01-01
uv run --extra export -m wattbikeexport.export --before 2026-01-01
uv run --extra export -m wattbikeexport.export \
  --output exports/2026-06-20
```

`--after` is inclusive and `--before` is exclusive. Dates and timestamps use
ISO 8601 and are converted to UTC.

For long-term use, prefer a new timestamped output directory for each run.
The DAL can combine these snapshots and deduplicate sessions by Wattbike
session ID.

## Raw archive

The exporter writes:

```text
wattbike-export/
  profile.json
  profile-objects.json
  sessions.json
  sessions/
    <timestamp>_<session-id>/
      metadata.json
      <files advertised by Wattbike sessionData>
```

Files currently advertised by Wattbike include FIT, TCX, WBS, and WBSR.
The exporter does not assume a fixed extension list, so it also retains lap or
segment files if Wattbike adds them to `sessionData`.

The export layer deliberately does not generate normalized revolution data.
WBS remains the authoritative raw source, and transformations belong in the
DAL. Completed downloads are reused. Interrupted downloads remain as `.part`
files and resume with an HTTP range request.

## Offline DAL

Inspect and verify an archive without `requests` or network access:

```console
uv run -m wattbikeexport.dal --source wattbike-export --verify
```

Multiple snapshots can be supplied in chronological order. If the same
session occurs more than once, the last snapshot wins:

```console
uv run -m wattbikeexport.dal \
  --source exports/2026-06-01 exports/2026-06-20
```

Programmatic access from the repository root:

```python
from wattbikeexport.dal import DAL

dal = DAL(["wattbike-export"])

for session in dal.sessions():
    print(session.start_time, session.title, session.summary)

    for revolution in session.revolutions():
        print(revolution.power, revolution.cadence, revolution.pes)
```

## Verification

```console
uv run --extra export -m wattbikeexport.export --help
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
[EXPORT.md](EXPORT.md) for the research and protocol details.
