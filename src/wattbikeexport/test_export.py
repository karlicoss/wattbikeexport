import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from wattbikeexport import common, export


class FakeResponse:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        return self.value

    def raise_for_status(self) -> None:
        return None


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


def test_existing_session_metadata_must_not_change() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "metadata.json"
        common.write_json(path, {"objectId": "session"})

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


def test_write_json_is_complete() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "nested" / "value.json"
        common.write_json(path, {"value": 1})
        assert json.loads(path.read_text()) == {"value": 1}
        assert not path.with_name("value.json.tmp").exists()


def test_safe_file_name_rejects_paths() -> None:
    with pytest.raises(AssertionError):
        common.safe_file_name("../ride.tcx")
