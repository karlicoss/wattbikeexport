import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import wattbike_export


class FakeResponse:
    def __init__(self, value):
        self.value = value
        self.status_code = 200
        self.headers = {}

    def json(self):
        return self.value

    def raise_for_status(self):
        return None


class ExportTest(unittest.TestCase):
    def test_csrf_parser(self):
        parser = wattbike_export.CsrfParser()
        parser.feed('<input type="hidden" name="_csrf" value="token">')
        self.assertEqual(parser.token, "token")

    def test_parse_datetime(self):
        self.assertEqual(
            wattbike_export.parse_datetime("2026-06-18"),
            "2026-06-18T00:00:00.000Z",
        )
        self.assertEqual(
            wattbike_export.parse_datetime("2026-06-18T12:30:00+01:00"),
            "2026-06-18T11:30:00.000Z",
        )

    def test_build_session_where(self):
        self.assertEqual(
            wattbike_export.build_session_where(
                user_id="user",
                after="2026-01-01T00:00:00.000Z",
                before="2027-01-01T00:00:00.000Z",
            ),
            {
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
            },
        )

    def test_session_files_are_recursive_and_deduplicated(self):
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
        self.assertEqual(
            wattbike_export.session_files(session),
            [
                ("sessionData.tcx", "ride.tcx"),
                ("sessionData.laps[0]", "lap-1.wbss"),
                ("sessionData.laps[1]", "lap-2.wbss"),
            ],
        )

    def test_build_revolution_export(self):
        self.assertEqual(
            wattbike_export.build_revolution_export(
                {
                    "laps": [
                        {
                            "data": [
                                {
                                    "time": "1.0",
                                    "power": "100",
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
                                {"time": "2.0", "power": "110"},
                            ]
                        }
                    ]
                }
            ),
            [
                {
                    "time": "1.0",
                    "power": "100",
                    "pesCombinedCoefficient": "0.5",
                    "pesRightCoefficient": "0.6",
                    "pesLeftCoefficient": "0.4",
                    "polarForces": "1,2,3",
                    "polarLeftCount": 2,
                    "polarTotalCount": 3,
                },
                {
                    "time": "2.0",
                    "power": "110",
                    "pesCombinedCoefficient": "na",
                    "pesRightCoefficient": "na",
                    "pesLeftCoefficient": "na",
                    "polarForces": "na",
                    "polarLeftCount": "na",
                    "polarTotalCount": "na",
                },
            ],
        )

    def test_list_sessions_paginates(self):
        client = wattbike_export.WattbikeClient.__new__(
            wattbike_export.WattbikeClient
        )
        client.user = {"objectId": "user"}
        client.parse_headers = {}
        client.request = Mock(
            side_effect=[
                FakeResponse(
                    {"results": [{"objectId": "a"}, {"objectId": "b"}]}
                ),
                FakeResponse({"results": [{"objectId": "c"}]}),
            ]
        )

        sessions = client.list_sessions(page_size=2)

        self.assertEqual([session["objectId"] for session in sessions], ["a", "b", "c"])
        self.assertEqual(client.request.call_count, 2)
        self.assertEqual(client.request.call_args_list[1].kwargs["params"]["skip"], 2)

    def test_get_profile_objects(self):
        client = wattbike_export.WattbikeClient.__new__(
            wattbike_export.WattbikeClient
        )
        client.user = {
            "performanceState": {
                "__type": "Pointer",
                "className": "UserPerformanceState",
                "objectId": "state",
            },
            "preferences": None,
        }
        client.get_object = Mock(return_value={"objectId": "state", "ftp": 250})

        self.assertEqual(
            client.get_profile_objects(),
            {"performanceState": {"objectId": "state", "ftp": 250}},
        )

    def test_write_json_is_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nested" / "value.json"
            wattbike_export.write_json(path, {"value": 1})
            self.assertEqual(json.loads(path.read_text()), {"value": 1})
            self.assertFalse(path.with_name("value.json.tmp").exists())

    def test_safe_file_name_rejects_paths(self):
        with self.assertRaises(AssertionError):
            wattbike_export.safe_file_name("../ride.tcx")


if __name__ == "__main__":
    unittest.main()
