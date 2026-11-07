import base64
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from wattbikeexport import export
from wattbikeexport.common import workout_directory_name
from wattbikeexport.dal import DAL
from wattbikeexport.tests.test_dal import _make_snapshot


def _workout(*, workout_id: str, legacy_id: str, start: str = "2026-11-03T10:52:28.000Z") -> dict[str, Any]:
    return {
        "id": workout_id,
        "legacyId": legacy_id,
        "status": "complete",
        "startDate": start,
        "totalSessionTime": 1000,
        "workoutWb": {
            "title": None,
            "time": None,
            "powerAvg": 200,
            "assets": {"wbsFileId": f"{workout_id}-wbs", "wbsrFileId": f"{workout_id}-wbsr"},
        },
    }


class FakeClient(export.WattbikeClient):
    def __init__(self, workouts: list[dict[str, Any]]) -> None:
        super().__init__(email="test@example.com", password="password")
        self.workouts = workouts
        self.profile = {"user": {"id": "new-user", "wbParseId": "user"}}
        self.downloads: list[str] = []

    def authenticate(self) -> None:
        return None

    def get(self, endpoint: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert endpoint == "users/me"
        assert params == {"join": "profileImageAsset"}
        return self.profile

    def list_workouts(self, *, page_size: int = 100) -> list[dict[str, Any]]:
        assert page_size > 0
        return self.workouts

    def download(self, *, asset_id: str, destination: Path) -> None:
        if destination.exists():
            return
        self.downloads.append(asset_id)
        destination.write_text(json.dumps({"laps": [{"data": [{"time": "1", "power": "100", "cadence": "80"}]}]}))


def _tree_state(root: Path) -> dict[Path, tuple[int, int, bytes | None]]:
    return {
        path.relative_to(root): (
            path.stat().st_mtime_ns,
            path.stat().st_ctime_ns,
            path.read_bytes() if path.is_file() else None,
        )
        for path in [root, *root.rglob("*")]
    }


def test_migration_leaves_archived_directories_untouched(tmp_path: Path) -> None:
    _make_snapshot(tmp_path)
    originals = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file() and p != tmp_path / "profile.json"}
    [legacy_directory] = list((tmp_path / "sessions").iterdir())
    legacy_state = _tree_state(legacy_directory)
    old = _workout(workout_id="migrated-uuid", legacy_id="session-id", start="2026-06-18T12:00:00.000Z")
    new = _workout(workout_id="new-uuid", legacy_id="1793700760078")
    client = FakeClient([old, new])

    export.export_account(client=client, output_directory=tmp_path)
    session_state = _tree_state(tmp_path / "sessions")
    export.export_account(client=client, output_directory=tmp_path)

    assert _tree_state(legacy_directory) == legacy_state
    assert _tree_state(tmp_path / "sessions") == session_state
    assert client.downloads == ["new-uuid-wbs", "new-uuid-wbsr"]
    assert all(p.read_bytes() == data for p, data in originals.items())
    assert len(list((tmp_path / "sessions").iterdir())) == 2
    assert len(list((tmp_path / "sessions").glob("*/metadata.json"))) == 2
    assert list(tmp_path.rglob("metadata-v2.json")) == []
    dal = DAL(tmp_path)
    dal.verify()
    sessions = list(dal.sessions())
    assert [s.id for s in sessions] == ["session-id", "new-uuid"]
    assert sessions[1].title == "Quick Ride"
    assert sessions[1].summary is not None
    assert sessions[1].summary["time"] == 1000
    assert [r.power for r in sessions[1].revolutions()] == [100]


def test_server_edits_do_not_change_archived_metadata(tmp_path: Path) -> None:
    workout = _workout(workout_id="uuid", legacy_id="old-id")
    client = FakeClient([workout])
    export.export_account(client=client, output_directory=tmp_path)
    previous = _tree_state(tmp_path / "sessions")
    workout["workoutWb"]["title"] = "Renamed"
    workout["workoutWb"]["assets"]["wbsFileId"] = "replacement-asset"
    workout["startDate"] = "2026-11-04T10:00:00.000Z"
    export.export_account(client=client, output_directory=tmp_path)
    assert _tree_state(tmp_path / "sessions") == previous
    assert client.downloads == ["uuid-wbs", "uuid-wbsr"]
    dal = DAL(tmp_path)
    dal.verify()
    [session] = list(dal.sessions())
    assert session.id == "uuid"
    assert session.title == "Quick Ride"
    assert session.start_time.isoformat() == "2026-11-03T10:52:28+00:00"
    assert next(session.revolutions()).power == 100
    [latest] = json.loads((tmp_path / "workouts.json").read_text())
    assert latest["workoutWb"]["title"] == "Renamed"


def test_incremental_export_and_dal_do_not_require_root_indexes(tmp_path: Path) -> None:
    _make_snapshot(tmp_path)
    (tmp_path / "sessions.json").unlink()
    client = FakeClient(
        [
            _workout(workout_id="uuid", legacy_id="session-id"),
            _workout(workout_id="new-uuid", legacy_id="1793700760078"),
        ]
    )
    export.export_account(client=client, output_directory=tmp_path)
    (tmp_path / "workouts.json").unlink()
    previous = _tree_state(tmp_path / "sessions")
    assert [s.id for s in DAL(tmp_path).sessions()] == ["session-id", "new-uuid"]
    export.export_account(client=client, output_directory=tmp_path)
    assert client.downloads == ["new-uuid-wbs", "new-uuid-wbsr"]
    assert _tree_state(tmp_path / "sessions") == previous


def test_profile_migration_preserves_one_legacy_copy(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"
    legacy_bytes = b'{"objectId": "user", "legacyOnly": true}\n'
    profile_path.write_bytes(legacy_bytes)
    assert DAL(tmp_path).snapshot.profile == json.loads(legacy_bytes)
    client = FakeClient([])

    export.export_account(client=client, output_directory=tmp_path)

    assert json.loads(profile_path.read_bytes()) == client.profile
    assert DAL(tmp_path).snapshot.profile == client.profile
    assert (tmp_path / "legacy" / "profile.json").read_bytes() == legacy_bytes
    legacy_state = _tree_state(tmp_path / "legacy")
    client.profile["user"]["displayName"] = "Updated"

    export.export_account(client=client, output_directory=tmp_path)

    assert json.loads(profile_path.read_bytes()) == client.profile
    assert _tree_state(tmp_path / "legacy") == legacy_state
    assert not (tmp_path / "profile-v2.json").exists()


def test_new_archive_has_one_current_profile(tmp_path: Path) -> None:
    client = FakeClient([])
    export.export_account(client=client, output_directory=tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["profile.json", "workouts.json"]
    assert DAL(tmp_path).snapshot.profile == client.profile


@pytest.mark.parametrize(
    "previous_profile",
    [{"objectId": "another-user"}, {"user": {"id": "another-user", "wbParseId": "user"}}],
)
def test_export_checks_account(tmp_path: Path, previous_profile: dict[str, Any]) -> None:
    (tmp_path / "profile.json").write_text(json.dumps(previous_profile))
    previous = _tree_state(tmp_path)
    with pytest.raises(AssertionError, match="different account"):
        export.export_account(client=FakeClient([]), output_directory=tmp_path)
    assert _tree_state(tmp_path) == previous


def test_failed_download_is_staged_and_resumed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index = tmp_path / "workouts.json"
    index.write_text("[]")
    profile_path = tmp_path / "profile.json"
    legacy_bytes = b'{"objectId": "user"}\n'
    profile_path.write_bytes(legacy_bytes)
    client = FakeClient([_workout(workout_id="uuid", legacy_id="1793700760078")])
    download = client.download

    def interrupted(*, asset_id: str, destination: Path) -> None:
        if asset_id == "uuid-wbsr":
            raise OSError("download interrupted")
        download(asset_id=asset_id, destination=destination)

    monkeypatch.setattr(client, "download", interrupted)
    with pytest.raises(OSError, match="download interrupted"):
        export.export_account(client=client, output_directory=tmp_path)
    assert index.read_text() == "[]"
    assert profile_path.read_bytes() == legacy_bytes
    assert not (tmp_path / "legacy").exists()
    assert not (tmp_path / "sessions").exists()
    assert len(list((tmp_path / ".partial").glob("*/*.wbs"))) == 1
    monkeypatch.setattr(client, "download", download)
    export.export_account(client=client, output_directory=tmp_path)
    assert client.downloads == ["uuid-wbs", "uuid-wbsr"]
    assert list((tmp_path / ".partial").iterdir()) == []
    assert len(list(DAL(tmp_path).sessions())) == 1
    assert (tmp_path / "legacy" / "profile.json").read_bytes() == legacy_bytes


def test_unfinished_workout_is_deferred(tmp_path: Path) -> None:
    workout = _workout(workout_id="uuid", legacy_id="1793700760078")
    workout["status"] = "processing"
    client = FakeClient([workout])
    export.export_account(client=client, output_directory=tmp_path)
    assert not (tmp_path / "sessions").exists()
    assert list(DAL(tmp_path).sessions()) == []
    workout["status"] = "complete"
    export.export_account(client=client, output_directory=tmp_path)
    assert len(list(DAL(tmp_path).sessions())) == 1


def test_unrecognized_existing_directory_is_not_modified(tmp_path: Path) -> None:
    workout = _workout(workout_id="uuid", legacy_id="1793700760078")
    directory = tmp_path / "sessions" / workout_directory_name(workout)
    directory.mkdir(parents=True)
    (directory / "unrecognized").write_text("keep me")
    previous = _tree_state(directory)
    with pytest.raises(AssertionError):
        export.export_account(client=FakeClient([workout]), output_directory=tmp_path)
    assert _tree_state(directory) == previous


@pytest.mark.parametrize(
    ("after", "before", "count"),
    [
        ("2026-11-03T10:52:28.000Z", None, 1),
        (None, "2026-11-03T10:52:28.000Z", 0),
        ("2026-11-03T10:52:28.001Z", None, 0),
    ],
)
def test_date_bounds(tmp_path: Path, after: str | None, before: str | None, count: int) -> None:
    export.export_account(
        client=FakeClient([_workout(workout_id="uuid", legacy_id="1793700760078")]),
        output_directory=tmp_path,
        after=after,
        before=before,
    )
    assert len(list(DAL(tmp_path).sessions())) == count


def test_pagination_and_duplicate_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    client = export.WattbikeClient(email="test@example.com", password="password")
    get = Mock(
        side_effect=[
            {"page": 1, "pageCount": 2, "total": 3, "data": [{"id": "a"}, {"id": "b"}]},
            {"page": 2, "pageCount": 2, "total": 3, "data": [{"id": "c"}]},
        ],
    )
    monkeypatch.setattr(client, "get", get)
    assert [w["id"] for w in client.list_workouts(page_size=2)] == ["a", "b", "c"]
    assert [call.kwargs["params"]["page"] for call in get.call_args_list] == [1, 2]
    get.side_effect = [{"page": 1, "pageCount": 1, "total": 2, "data": [{"id": "a"}, {"id": "a"}]}]
    with pytest.raises(AssertionError):
        client.list_workouts()


@pytest.mark.parametrize("valid_state", [True, False])
def test_oauth_pkce_and_state(monkeypatch: pytest.MonkeyPatch, *, valid_state: bool) -> None:
    client = export.WattbikeClient(email="test@example.com", password="password")
    authorize: dict[str, Any] = {}

    def request(method: str, url: str, **kwargs: Any) -> Mock:
        if url.endswith("/oauth2/authorize"):
            authorize.update(kwargs["params"])
            return Mock(url="https://auth.wattbike.com/login", text='<input name="_csrf" value="csrf">')
        if url.endswith("/login"):
            assert kwargs["allow_redirects"] is False
            state = authorize["state"] if valid_state else "wrong-state"
            return Mock(is_redirect=True, headers={"Location": f"{export._AUTH_REDIRECT_URI}?state={state}&code=code"})
        assert url.endswith("/oauth2/token")
        assert method == "POST"
        verifier = kwargs["data"]["code_verifier"]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        assert challenge == authorize["code_challenge"]
        return Mock(json=Mock(return_value={"access_token": "private-token"}))

    monkeypatch.setattr(client, "request", request)
    if valid_state:
        client.authenticate()
        assert client.headers["Authorization"] == "Bearer private-token"
        assert "Authorization" not in client.http.headers
    else:
        with pytest.raises(AssertionError):
            client.authenticate()


def test_workout_directory_rejects_path_traversal() -> None:
    with pytest.raises(AssertionError):
        workout_directory_name(_workout(workout_id="../outside", legacy_id="old"))


def test_parse_datetime() -> None:
    assert export._parse_datetime("2026-06-18") == "2026-06-18T00:00:00.000Z"
    assert export._parse_datetime("2026-06-18T12:30:00") == "2026-06-18T12:30:00.000Z"
    assert export._parse_datetime("2026-06-18T12:30:00Z") == "2026-06-18T12:30:00.000Z"
    assert export._parse_datetime("2026-06-18T12:30:00+01:00") == "2026-06-18T11:30:00.000Z"


def test_cli_exports_current_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    credentials = {"email": "test@example.com", "password": "password"}
    secrets_path = tmp_path / "secrets.json"
    secrets_path.write_text(json.dumps(credentials))
    output = tmp_path / "archive"
    client = FakeClient([_workout(workout_id="uuid", legacy_id="old-id")])
    constructor = Mock(return_value=client)
    monkeypatch.setattr(export, "WattbikeClient", constructor)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "wattbikeexport",
            "--secret",
            str(secrets_path),
            "--output",
            str(output),
            "--after",
            "2026-11-03",
            "--before",
            "2026-11-04",
        ],
    )

    export.main()

    constructor.assert_called_once_with(**credentials)
    assert client.downloads == ["uuid-wbs", "uuid-wbsr"]
    assert DAL(output).snapshot.profile == client.profile
    assert [session.id for session in DAL(output).sessions()] == ["uuid"]
