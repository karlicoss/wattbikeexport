from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest

from wattbikeexport import common, export


class FakeResponse:
    def __init__(self, value: Any, *, status_code: int = 200) -> None:
        self.value = value
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        return self.value

    def raise_for_status(self) -> None:
        return None


class FakeExportClient:
    def __init__(self, *, source_names: list[str], field: str = "wbss") -> None:
        self.field = field
        self.source_names = source_names

    def authenticate(self) -> None:
        return None

    def get_profile(self) -> dict[str, Any]:
        return {"objectId": "user"}

    def get_profile_objects(self) -> dict[str, Any]:
        return {}

    def list_sessions(
        self,
        *,
        after: str | None = None,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        assert after is None
        assert before is None
        return [
            {
                "objectId": "session",
                "startDate": {
                    "__type": "Date",
                    "iso": "2026-06-18T12:00:00.000Z",
                },
                "sessionData": self.session_data(),
            }
        ]

    def session_data(self) -> dict[str, Any]:
        if self.field == "wbss":
            return {
                "wbss": [
                    {
                        "name": source_name,
                    }
                    for source_name in self.source_names
                ],
            }
        assert len(self.source_names) == 1, self.source_names
        return {self.field: {"name": self.source_names[0]}}

    def download(self, *, source_name: str, destination: Path) -> bool:
        assert source_name in self.source_names
        assert destination.name == source_name
        return False


def test_csrf_parser() -> None:
    parser = export.CsrfParser()
    parser.feed('<input type="hidden" name="_csrf" value="token">')
    assert parser.token == "token"


def test_parse_datetime() -> None:
    assert export._parse_datetime("2026-06-18") == "2026-06-18T00:00:00.000Z"
    assert export._parse_datetime("2026-06-18T12:30:00") == "2026-06-18T12:30:00.000Z"
    assert export._parse_datetime("2026-06-18T12:30:00Z") == "2026-06-18T12:30:00.000Z"
    assert export._parse_datetime("2026-06-18T12:30:00+01:00") == "2026-06-18T11:30:00.000Z"


def test_build_session_where() -> None:
    assert export._build_session_where(
        user_id="user",
        after="2026-01-01T00:00:00.000Z",
        before="2027-01-01T00:00:00.000Z",
    ) == {
        "user": {
            "__type": "Pointer",
            "className": "_User",
            "objectId": "user",
        },
        "startDate": {
            "$gte": {
                "__type": "Date",
                "iso": "2026-01-01T00:00:00.000Z",
            },
            "$lt": {
                "__type": "Date",
                "iso": "2027-01-01T00:00:00.000Z",
            },
        },
    }


def test_existing_session_metadata_must_not_change(tmp_path: Path) -> None:
    path = tmp_path / "metadata.json"
    export._write_json(path, {"objectId": "session"})

    export._assert_unchanged_metadata(
        path=path,
        session={"objectId": "session"},
    )
    with pytest.raises(AssertionError):
        export._assert_unchanged_metadata(
            path=path,
            session={"objectId": "session", "title": "Changed"},
        )


def test_session_files_are_recursive_and_deduplicated() -> None:
    """Find nested source files once while retaining their first field path."""
    session = {
        "sessionData": {
            "tcx": {"name": "ride.tcx"},
            "laps": [
                {"name": "lap-1.wbss"},
                {"name": "lap-2.wbss"},
                {"name": "ride.tcx"},
            ],
        }
    }
    assert common.session_files(session) == [
        ("sessionData.tcx", "ride.tcx"),
        ("sessionData.laps[0]", "lap-1.wbss"),
        ("sessionData.laps[1]", "lap-2.wbss"),
    ]


def test_list_sessions_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    client = export.WattbikeClient.__new__(export.WattbikeClient)
    client.user = {"objectId": "user"}
    client.parse_headers = {}
    monkeypatch.setattr(
        client,
        "request",
        Mock(
            side_effect=[
                FakeResponse({"results": [{"objectId": "a"}, {"objectId": "b"}]}),
                FakeResponse({"results": [{"objectId": "c"}]}),
            ]
        ),
    )

    sessions = client.list_sessions(page_size=2)

    assert [session["objectId"] for session in sessions] == ["a", "b", "c"]


def test_get_profile_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    client = export.WattbikeClient.__new__(export.WattbikeClient)
    client.user = {
        "performanceState": {
            "__type": "Pointer",
            "className": "UserPerformanceState",
            "objectId": "state",
        },
        "preferences": None,
        "coach": {
            "__type": "Pointer",
            "className": "_User",
            "objectId": "coach",
        },
    }
    monkeypatch.setattr(
        client,
        "get_object",
        Mock(
            side_effect=[
                {"objectId": "state", "ftp": 250},
                {"objectId": "coach"},
            ]
        ),
    )

    assert client.get_profile_objects() == {
        "performanceState": {"objectId": "state", "ftp": 250},
        "coach": {"objectId": "coach"},
    }


def test_get_profile_removes_session_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client = export.WattbikeClient.__new__(export.WattbikeClient)
    client.parse_headers = {}
    monkeypatch.setattr(
        client,
        "request",
        Mock(
            return_value=FakeResponse(
                {
                    "objectId": "user",
                    "sessionToken": "secret",
                }
            )
        ),
    )

    profile = client.get_profile()

    assert profile == {"objectId": "user"}
    assert client.user is not None
    assert "sessionToken" not in client.user


def test_download_returns_false_for_missing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = export.WattbikeClient.__new__(export.WattbikeClient)
    client.parse_headers = {}
    monkeypatch.setattr(
        client,
        "request",
        Mock(return_value=FakeResponse({}, status_code=404)),
    )

    destination = tmp_path / "missing.wbss"

    assert (
        client.download(
            source_name="missing.wbss",
            destination=destination,
        )
        is False
    )
    assert not destination.exists()


def test_export_skips_empty_lap_wbss_quirk(tmp_path: Path) -> None:
    export.export_account(
        client=cast(
            export.WattbikeClient,
            FakeExportClient(
                source_names=[
                    "user_session_0-1000.wbss",
                    "user_session_1000-1000.wbss",
                ]
            ),
        ),
        output_directory=tmp_path,
    )

    [session_directory] = list((tmp_path / "sessions").iterdir())
    assert [path.name for path in session_directory.iterdir()] == ["metadata.json"]


def test_export_rejects_missing_wbss_without_empty_lap_quirk(tmp_path: Path) -> None:
    with pytest.raises(AssertionError):
        export.export_account(
            client=cast(export.WattbikeClient, FakeExportClient(source_names=["missing.wbss"])),
            output_directory=tmp_path,
        )


def test_export_rejects_missing_core_file(tmp_path: Path) -> None:
    with pytest.raises(AssertionError):
        export.export_account(
            client=cast(
                export.WattbikeClient,
                FakeExportClient(source_names=["missing.wbs"], field="wbs"),
            ),
            output_directory=tmp_path,
        )


def test_safe_file_name_rejects_paths() -> None:
    with pytest.raises(AssertionError):
        common.safe_file_name("../ride.tcx")
