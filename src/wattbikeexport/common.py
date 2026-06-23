from __future__ import annotations

from typing import Any

type Json = Any
type JsonObject = dict[str, Json]


def session_files(session: JsonObject) -> list[tuple[str, str]]:
    """Return unique Wattbike file references from nested sessionData."""
    file_references: list[tuple[str, str]] = []

    def walk(value: Json, *, field: str) -> None:
        if isinstance(value, dict):
            name = value.get("name")
            if isinstance(name, str):
                file_references.append((field, name))
            for key, child in value.items():
                if key != "name":
                    walk(child, field=f"{field}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, field=f"{field}[{index}]")

    walk(session.get("sessionData", {}), field="sessionData")

    seen = set()
    files = []
    for field, name in file_references:
        if name not in seen:
            files.append((field, name))
            seen.add(name)
    return files


def safe_file_name(name: str) -> str:
    """Reject server-provided names that could escape a session directory."""
    assert name not in {"", ".", ".."}, name
    assert "/" not in name, name
    assert "\\" not in name, name
    return name


def session_directory_name(session: JsonObject) -> str:
    """Return a stable, sortable directory name for a Wattbike session."""
    start_date = session["startDate"]["iso"]
    assert isinstance(start_date, str), start_date
    timestamp = start_date.replace("-", "").replace(":", "").replace(".000", "")
    return f"{timestamp}_{session['objectId']}"
