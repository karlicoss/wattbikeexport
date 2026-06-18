# Wattbike Hub exporter

This repository contains a self-contained
[PEP 723](https://peps.python.org/pep-0723/) script. `uv` installs `requests`
from the inline metadata and runs the exporter without a separate virtual
environment setup step.

Create `secrets.json`:

```json
{
  "email": "name@example.com",
  "password": "your Wattbike password"
}
```

Then run:

```console
uv run wattbike_export.py
```

Useful options:

```console
uv run wattbike_export.py --after 2026-01-01
uv run wattbike_export.py --before 2026-01-01
uv run wattbike_export.py --metadata-only
uv run wattbike_export.py --output another-directory
```

`--after` is inclusive and `--before` is exclusive. Dates and timestamps use
ISO 8601 and are converted to UTC.

The archive contains:

```text
wattbike-export/
  manifest.json
  profile.json
  profile-objects.json
  sessions.json
  sessions/
    <timestamp>_<session-id>/
      metadata.json
      revolutions.json
      <files advertised by Wattbike sessionData>
```

Files currently advertised by Wattbike include FIT, TCX, WBS, and WBSR.
The exporter does not assume a fixed extension list, so it also retains lap or
segment files if Wattbike adds them to `sessionData`.

`revolutions.json` is derived from WBS using the same field selection as
Wattbike Hub's JSON export. It retains per-revolution power, cadence, speed,
balance, PES, polar-force, and available heart-rate values while the original
WBS remains the authoritative source.

`profile-objects.json` resolves the performance-state, preferences, and
statistics objects referenced by the main profile.

Completed downloads are reused. Interrupted downloads remain as `.part` files
and resume with an HTTP range request on the next run. The manifest records
the size and SHA-256 digest of each completed file.

Run the offline tests with:

```console
uv run wattbike_export.py --help
uv run --with requests python -m unittest discover -s tests
```

The API and authentication flow are undocumented and may change. See
[EXPORT.md](EXPORT.md) for the research and design rationale.
