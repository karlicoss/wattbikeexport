from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

type Json = Any
type JsonObject = dict[str, Json]


# TODO let's get rid of this -- just call json.loads directly?
# Addressed: the load_json wrapper was removed; callers use json.loads directly.
def write_json(path: Path, value: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w") as output:
        json.dump(value, output, indent=2, ensure_ascii=False)
        output.write("\n")
    temporary.replace(path)


def iter_named_files(
    value: Json,
    location: str = "sessionData",
) -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str):
            yield location, name
        for key, child in value.items():
            if key != "name":
                yield from iter_named_files(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_named_files(child, f"{location}[{index}]")


def session_files(session: JsonObject) -> list[tuple[str, str]]:
    seen = set()
    files = []
    for field, name in iter_named_files(session.get("sessionData", {})):
        if name not in seen:
            files.append((field, name))
            seen.add(name)
    return files


# TODO why do we need this? leave a comment/docstring
def safe_file_name(name: str) -> str:
    """Reject server-provided names that could escape a session directory."""
    assert name not in {"", ".", ".."}, name
    assert "/" not in name, name
    assert "\\" not in name, name
    return name


# TODO leave a docstring
def session_directory_name(session: JsonObject) -> str:
    """Return a stable, sortable directory name for a Wattbike session."""
    start_date = session["startDate"]["iso"]
    assert isinstance(start_date, str), start_date
    timestamp = start_date.replace("-", "").replace(":", "").replace(".000", "")
    return f"{timestamp}_{session['objectId']}"
