import tempfile
import warnings
from pathlib import Path

from wattbikeexport.common import session_directory_name, write_json
from wattbikeexport.dal import DAL


def _make_snapshot(root: Path, *, title: str = "Quick Ride") -> None:
    session_id = "session-id"
    session = {
        "objectId": session_id,
        "startDate": {
            "__type": "Date",
            "iso": "2026-06-18T12:00:00.000Z",
        },
        "title": title,
        "sessionSummary": {
            "__type": "Object",
            "className": "RideSessionSummary",
            "powerAvg": 200,
        },
        "sessionData": {
            "wbs": {"name": "ride.wbs"},
            "wbsr": {"name": "ride.wbsr"},
        },
    }
    session_root = root / "sessions" / session_directory_name(session)
    wbs_path = session_root / "ride.wbs"
    write_json(
        wbs_path,
        {
            "laps": [
                {
                    "data": [
                        {
                            "time": "1.0",
                            "power": "100",
                            "cadence": "80",
                            "pes": {
                                "combinedCoefficient": "0.5",
                                "rightCoefficient": "0.6",
                                "leftCoefficient": "0.4",
                            },
                            "polar": {
                                "force": "1,2,3",
                                "lcnt": 2,
                                "cnt": 3,
                            },
                        },
                        {"time": "2.0", "power": "110", "cadence": "82"},
                    ]
                }
            ]
        },
    )
    wbsr_path = session_root / "ride.wbsr"
    write_json(wbsr_path, {"revolutions": []})

    write_json(root / "profile.json", {"objectId": "user"})
    write_json(root / "profile-objects.json", {})
    write_json(root / "sessions.json", [session])


def test_offline_session_access_and_verification() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        _make_snapshot(root)

        dal = DAL([root])
        dal.verify()
        [session] = list(dal.sessions())
        revolutions = list(session.revolutions())
        summary = session.summary
        assert summary is not None

        assert session.title == "Quick Ride"
        assert summary["powerAvg"] == 200
        assert revolutions[0].power == 100
        assert revolutions[0].cadence == 80
        assert revolutions[0].pes == 0.5
        assert revolutions[0].polar_forces == (1, 2, 3)
        assert revolutions[1].pes is None
        assert revolutions[1].polar_forces is None


def test_later_snapshot_wins() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        first = base / "first"
        second = base / "second"
        _make_snapshot(first, title="Old title")
        _make_snapshot(second, title="New title")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            sessions = list(DAL([first, second]).sessions())

        assert len(caught) == 1
        assert caught[0].category is UserWarning
        assert [session.title for session in sessions] == ["New title"]
