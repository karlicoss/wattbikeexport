from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .common import (
    JsonObject,
    safe_file_name,
    session_directory_name,
    session_files,
)


def _number(value: str | float | None) -> float:
    assert value is not None, value
    assert value != "na", value
    return float(value)


def _optional_number(value: str | float | None) -> float | None:
    if value is None or value == "na":
        return None
    return float(value)


@dataclass(frozen=True)
class Snapshot:
    root: Path
    profile: JsonObject
    profile_objects: dict[str, JsonObject]
    session_records: list[JsonObject]
    source_files: dict[tuple[str, str], Path]

    @classmethod
    def load(cls, source: Path | str) -> Snapshot:
        root = Path(source)
        if root.is_file():
            root = root.parent
        root = root.resolve()

        profile = json.loads((root / "profile.json").read_text())
        profile_objects_path = root / "profile-objects.json"
        profile_objects = json.loads(profile_objects_path.read_text()) if profile_objects_path.exists() else {}
        session_records = json.loads((root / "sessions.json").read_text())
        assert isinstance(session_records, list), session_records

        source_files = {}
        for session in session_records:
            session_root = root / "sessions" / session_directory_name(session)
            for field, name in session_files(session):
                key = (session["objectId"], field)
                source_files[key] = session_root / safe_file_name(name)

        return cls(
            root=root,
            profile=profile,
            profile_objects=profile_objects,
            session_records=session_records,
            source_files=source_files,
        )

    def verify(self) -> None:
        for path in self.source_files.values():
            assert path.is_file(), path


@dataclass(frozen=True)
class Revolution:
    raw: JsonObject

    @property
    def time(self) -> float:
        return _number(self.raw["time"])

    @property
    def power(self) -> float:
        return _number(self.raw["power"])

    @property
    def cadence(self) -> float:
        return _number(self.raw["cadence"])

    @property
    def heartrate(self) -> float | None:
        # WBS omits heartrate when no heart-rate monitor was connected.
        return _optional_number(self.raw.get("heartrate"))

    @property
    def pes(self) -> float | None:
        # Initial revolutions can lack PES data while the bike starts measuring.
        value = self.raw.get("pes")
        if value is None:
            return None
        assert isinstance(value, dict), value
        return _number(value["combinedCoefficient"])

    @property
    def polar_forces(self) -> tuple[int, ...] | None:
        # Polar force data is absent for the same initial revolutions as PES.
        value = self.raw.get("polar")
        if value is None:
            return None
        assert isinstance(value, dict), value
        force = value.get("force")
        assert isinstance(force, str), force
        return tuple(map(int, force.split(",")))


@dataclass(frozen=True)
class Session:
    snapshot: Snapshot
    raw: JsonObject

    @property
    def id(self) -> str:
        value = self.raw["objectId"]
        assert isinstance(value, str), value
        return value

    @property
    def title(self) -> str:
        value = self.raw.get("title", "Untitled")
        assert isinstance(value, str), value
        return value

    @property
    def start_time(self) -> datetime:
        return datetime.fromisoformat(self.raw["startDate"]["iso"])

    @property
    def summary(self) -> JsonObject | None:
        value = self.raw.get("sessionSummary")
        if isinstance(value, dict) and value.get("__type") == "Object":
            return value
        return None

    def source_path(self, field: str) -> Path:
        return self.snapshot.source_files[(self.id, field)]

    def source_json(self, field: str) -> JsonObject:
        value = json.loads(self.source_path(field).read_text())
        assert isinstance(value, dict), value
        return value

    def revolutions(self) -> Iterator[Revolution]:
        wbs = self.source_json("sessionData.wbs")
        for lap in wbs.get("laps", []):
            for row in lap.get("data", []):
                yield Revolution(raw=row)


class DAL:
    """Offline access to one Wattbike export snapshot."""

    def __init__(self, source: Path | str) -> None:
        self.snapshot = Snapshot.load(source)

    def sessions(self) -> Iterator[Session]:
        sessions = [Session(snapshot=self.snapshot, raw=raw) for raw in self.snapshot.session_records]
        yield from sorted(sessions, key=lambda session: session.start_time)

    def verify(self) -> None:
        self.snapshot.verify()


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect Wattbike exports offline through the DAL.")
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Wattbike export directory or a file within it",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify that every source file advertised by session metadata exists",
    )
    return parser


def main() -> None:
    args = _make_parser().parse_args()
    dal = DAL(args.source)
    if args.verify is True:
        dal.verify()

    sessions = list(dal.sessions())
    print(f"Source: {dal.snapshot.root}")
    print(f"Sessions: {len(sessions)}")
    if len(sessions) > 0:
        print(f"Range: {sessions[0].start_time.isoformat()} to {sessions[-1].start_time.isoformat()}")
        print(f"Revolutions: {sum(1 for session in sessions for _ in session.revolutions())}")


if __name__ == "__main__":
    main()
