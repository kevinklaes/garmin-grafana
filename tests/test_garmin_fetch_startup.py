"""Unit tests for the garmin_fetch startup path and the merged activity loop.

garmin_fetch connects to the database and writes a DemoPoint at import time,
so every test imports a fresh copy of the module with the InfluxDB client
classes patched. No network, no InfluxDB and no Garmin account is needed.

Run from the repo root with:
    python3 -m unittest discover tests
"""
import importlib
import io
import logging
import os
import sys
import unittest
import zipfile
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from influxdb.exceptions import InfluxDBClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

MODULE = "garmin_grafana.garmin_fetch"
POSTGRES_MODULES = ("psycopg", "garmin_grafana.postgres_backend", "postgres_backend")


class GarminFetchImportCase(unittest.TestCase):
    """Imports garmin_fetch fresh, with a controlled environment and fake clients."""

    def import_garmin_fetch(self, env, v1_client=None):
        self.v1_class = MagicMock(name="InfluxDBClient")
        if v1_client is not None:
            self.v1_class.return_value = v1_client
        self.v3_class = MagicMock(name="InfluxDBClient3")

        # The module replaces the root logging handlers on import: put them back.
        saved_handlers = logging.root.handlers[:]
        saved_level = logging.root.level
        self.addCleanup(logging.root.setLevel, saved_level)
        self.addCleanup(logging.root.handlers.__setitem__, slice(None), saved_handlers)

        # Start every import from a clean slate, and leave one behind.
        saved_modules = {name: sys.modules.pop(name) for name in (MODULE, *POSTGRES_MODULES) if name in sys.modules}
        self.addCleanup(sys.modules.update, saved_modules)
        self.addCleanup(sys.modules.pop, MODULE, None)

        patches = [
            patch.dict(os.environ, env, clear=True),
            patch("dotenv.load_dotenv", return_value=False), # ignore any local override-default-vars.env
            patch("influxdb.InfluxDBClient", self.v1_class),
            patch("influxdb_client_3.InfluxDBClient3", self.v3_class),
            patch("builtins.print"), # the banner
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return importlib.import_module(MODULE)


class TestStartupDefaultBackend(GarminFetchImportCase):
    ENV = {
        "INFLUXDB_HOST": "influx.test",
        "INFLUXDB_PORT": "8086",
        "INFLUXDB_USERNAME": "test-user",
        "INFLUXDB_PASSWORD": "test-password",
        "INFLUXDB_DATABASE": "TestStats",
    }

    def test_backend_defaults_to_influxdb_and_postgres_is_not_loaded(self):
        garmin_fetch = self.import_garmin_fetch(self.ENV)

        self.assertEqual(garmin_fetch.DATABASE_BACKEND, "influxdb")
        for name in POSTGRES_MODULES:
            self.assertNotIn(name, sys.modules)
        self.assertIs(garmin_fetch.influxdbclient, self.v1_class.return_value)
        self.v3_class.assert_not_called()

    def test_explicit_influxdb_backend_behaves_like_the_default(self):
        garmin_fetch = self.import_garmin_fetch({**self.ENV, "DATABASE_BACKEND": "InfluxDB"})

        self.assertEqual(garmin_fetch.DATABASE_BACKEND, "influxdb")
        self.assertNotIn("psycopg", sys.modules)
        self.v1_class.assert_called_once()

    def test_client_gets_exactly_the_credentials_from_the_environment(self):
        self.import_garmin_fetch(self.ENV)

        self.v1_class.assert_called_once_with(
            host="influx.test", port=8086, username="test-user", password="test-password"
        )
        self.v1_class.return_value.switch_database.assert_called_once_with("TestStats")

    def test_https_endpoint_gets_the_same_credentials(self):
        self.import_garmin_fetch({**self.ENV, "INFLUXDB_ENDPOINT_IS_HTTP": "False"})

        self.v1_class.assert_called_once_with(
            host="influx.test", port=8086, username="test-user", password="test-password",
            ssl=True, verify_ssl=True,
        )

    def test_demo_point_is_written_once_on_start(self):
        self.import_garmin_fetch(self.ENV)

        write_points = self.v1_class.return_value.write_points
        write_points.assert_called_once()
        (points,), _ = write_points.call_args
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["measurement"], "DemoPoint")
        self.assertEqual(points[0]["tags"], {"DemoTag": "DemoTagValue"})
        self.assertEqual(points[0]["fields"], {"DemoField": 0})

    def test_rejected_credentials_abort_startup(self):
        client = MagicMock(name="client")
        client.write_points.side_effect = InfluxDBClientError("authorization failed", 401)

        with self.assertRaises(InfluxDBClientError):
            self.import_garmin_fetch(self.ENV, v1_client=client)
        self.assertNotIn(MODULE, sys.modules)


class FakeFitFile:
    """Stands in for fitparse.FitFile: the 'FIT bytes' name a canned message set."""

    MESSAGES = {}

    def __init__(self, buffer):
        self.messages = self.MESSAGES[buffer.read().decode()]

    def parse(self):
        pass

    def get_messages(self, name):
        return [SimpleNamespace(get_values=lambda values=values: values) for values in self.messages.get(name, [])]


def fit_zip(key):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("activity.fit", key)
    return buffer.getvalue()


class TestActivityBatch(GarminFetchImportCase):
    """Upstream's skip-and-continue (#295) together with the fork's Di2 and CORE fields."""

    def setUp(self):
        self.garmin_fetch = self.import_garmin_fetch({
            **TestStartupDefaultBackend.ENV,
            "FETCH_SELECTION": "activity,gear_shifts",
        })
        FakeFitFile.MESSAGES = {
            "ride-1": self.ride(datetime(2026, 1, 1, 8, 0, 0), rear_gear=21),
            "ride-3": self.ride(datetime(2026, 1, 3, 8, 0, 0), rear_gear=17),
        }
        fit_patch = patch.object(self.garmin_fetch, "FitFile", FakeFitFile)
        fit_patch.start()
        self.addCleanup(fit_patch.stop)

        formats = SimpleNamespace(ORIGINAL="original", TCX="tcx")

        def download_activity(activity_id, dl_fmt):
            if activity_id == 2:
                if dl_fmt == formats.ORIGINAL:
                    return self.empty_zip()
                raise requests.exceptions.Timeout("too large")
            return fit_zip(f"ride-{activity_id}")

        self.garmin_fetch.garmin_obj = SimpleNamespace(
            ActivityDownloadFormat=formats, download_activity=download_activity
        )

    @staticmethod
    def empty_zip():
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w"):
            pass
        return buffer.getvalue() # no .fit entry -> FIT path falls back to TCX

    @staticmethod
    def ride(start, rear_gear):
        return {
            "record": [{"timestamp": start, "heart_rate": 120, "core_temperature": 37.4, "skin_temperature": 33.1}],
            "session": [{"timestamp": start, "start_time": start, "sport": "cycling", "sub_sport": "road", "unknown_110": "Gravel"}],
            "lap": [{"timestamp": start, "start_time": start, "sport": "cycling"}],
            "event": [{"event": "rear_gear_change", "timestamp": start, "rear_gear": rear_gear, "rear_gear_num": 5}],
        }

    def test_failed_activity_is_skipped_and_the_rest_keep_fork_fields(self):
        points = self.garmin_fetch.fetch_activity_GPS({1: "cycling", 2: "cycling", 3: "cycling"})

        def activity_ids(measurement):
            return [p["tags"]["ActivityID"] for p in points if p["measurement"] == measurement]

        # upstream #295: the timed-out activity 2 does not drop activities 1 and 3
        self.assertEqual(activity_ids("ActivityGPS"), [1, 3])
        self.assertEqual(self.garmin_fetch.PARSED_ACTIVITY_ID_LIST, [1, 3])
        # fork: Di2 gear shifts and CORE temperature still ride along
        self.assertEqual(activity_ids("GearShiftEvents"), [1, 3])
        gps = next(p for p in points if p["measurement"] == "ActivityGPS")
        self.assertEqual(gps["fields"]["CoreTemperature"], 37.4)
        self.assertEqual(gps["fields"]["SkinTemperature"], 33.1)
        # upstream #289 and #277
        session = next(p for p in points if p["measurement"] == "ActivitySession")
        self.assertEqual(session["fields"]["Sport_Profile_Name"], "Gravel")
        lap = next(p for p in points if p["measurement"] == "ActivityLap")
        self.assertEqual(lap["fields"]["Sport"], "cycling")


if __name__ == "__main__":
    unittest.main()
