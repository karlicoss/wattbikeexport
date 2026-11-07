from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from hpikit.json import Json, loads


def session_metadata(root: Path) -> Iterator[tuple[Path, Json]]:
    for path in sorted((root / "sessions").glob("*/metadata.json")):
        value = loads(path.read_bytes())
        assert isinstance(value, dict), path
        yield path.parent, value


def session_files(session: Json) -> list[tuple[str, str]]:
    """Return unique Wattbike file references from nested sessionData."""
    file_references: list[tuple[str, str]] = []

    def walk(value: Any, *, field: str) -> None:
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


def workout_directory_name(workout: Json) -> str:
    timestamp = workout["startDate"].replace("-", "").replace(":", "").replace(".000", "")
    return safe_file_name(f"{timestamp}_{safe_file_name(workout['id'])}")


def workout_files(workout: Json) -> list[tuple[str, str, str]]:
    """Return (format, asset ID, archive filename) for a new API workout."""
    files = []
    for field, asset_id in workout["workoutWb"]["assets"].items():
        if asset_id is None:
            continue
        assert field.endswith("FileId"), field
        assert isinstance(asset_id, str), asset_id
        extension = safe_file_name(field.removesuffix("FileId").lower())
        name = f"{safe_file_name(asset_id)}.{extension}"
        files.append((extension, asset_id, name))
    return files
