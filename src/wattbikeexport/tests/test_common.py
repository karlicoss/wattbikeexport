import pytest

from wattbikeexport import common


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


def test_safe_file_name_rejects_paths() -> None:
    with pytest.raises(AssertionError):
        common.safe_file_name("../ride.tcx")
