"""Unit tests for backfill_core_temperature (CORE sensor historical backfill script).

Run from the repo root with:
    python3 -m unittest discover tests
"""
import datetime
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from garmin_grafana import backfill_core_temperature as bct


class TestActivityIdFromFilename(unittest.TestCase):
    def test_extracts_numeric_id(self):
        self.assertEqual(
            bct.activity_id_from_filename("kevin.klaes@gmail.com_1002111179.fit"),
            "1002111179",
        )

    def test_extracts_id_ignoring_tap_sync_suffix(self):
        # Some export entries carry extra "_tap-sync-<n>-<hash>" data after the
        # activity ID -- the ID is the first digit run after the email, not
        # necessarily the digits immediately before ".fit".
        self.assertEqual(
            bct.activity_id_from_filename(
                "kevin.klaes@gmail.com_5983748945_tap-sync-16984-3c381e52af6f985ffd5f11889891870c.fit"
            ),
            "5983748945",
        )

    def test_returns_none_for_non_matching_filename(self):
        self.assertIsNone(bct.activity_id_from_filename("kevin.klaes@gmail.com_gear.json"))
        self.assertIsNone(bct.activity_id_from_filename("readme.txt"))
        self.assertIsNone(bct.activity_id_from_filename("kevin.klaes@gmail.com_notanumber.fit"))


class TestCoreTemperaturePoint(unittest.TestCase):
    def test_builds_partial_field_point_for_record_with_core_data(self):
        record = {
            'timestamp': datetime.datetime(2020, 1, 1, 12, 0, 0),
            'core_temperature': 37.2,
            'skin_temperature': 33.1,
            'heart_rate': 140,  # must NOT appear in fields -- partial write only
        }
        point = bct.core_temperature_point(
            record, activity_id="123", activity_selector="20200101T120000UTC-cycling",
            device="TestDevice", database="TestDB",
        )
        self.assertEqual(point["measurement"], "ActivityGPS")
        self.assertEqual(point["tags"], {
            "Device": "TestDevice",
            "Database_Name": "TestDB",
            "ActivityID": "123",
            "ActivitySelector": "20200101T120000UTC-cycling",
        })
        self.assertEqual(point["fields"], {"CoreTemperature": 37.2, "SkinTemperature": 33.1})

    def test_returns_none_for_record_without_core_data(self):
        record = {'timestamp': datetime.datetime(2020, 1, 1), 'heart_rate': 140}
        result = bct.core_temperature_point(
            record, activity_id="1", activity_selector="x", device="d", database="db",
        )
        self.assertIsNone(result)

    def test_returns_none_for_record_without_timestamp(self):
        record = {'core_temperature': 37.0}
        result = bct.core_temperature_point(
            record, activity_id="1", activity_selector="x", device="d", database="db",
        )
        self.assertIsNone(result)


class TestLookupActivitySelector(unittest.TestCase):
    class _FakeResultV1:
        def __init__(self, points):
            self._points = points

        def get_points(self):
            return iter(self._points)

    class _FakeInfluxClientV1:
        def __init__(self, points):
            self._points = points

        def query(self, query):
            return TestLookupActivitySelector._FakeResultV1(self._points)

    def test_returns_existing_tag_when_point_found(self):
        client = self._FakeInfluxClientV1([{"ActivitySelector": "20200101T000000UTC-road_biking"}])
        result = bct.lookup_activity_selector(client, "1", "999")
        self.assertEqual(result, "20200101T000000UTC-road_biking")

    def test_returns_none_when_no_existing_point(self):
        client = self._FakeInfluxClientV1([])
        result = bct.lookup_activity_selector(client, "1", "999")
        self.assertIsNone(result)

    def test_returns_none_and_does_not_raise_on_query_error(self):
        class _RaisingClient:
            def query(self, query):
                raise RuntimeError("connection lost")

        result = bct.lookup_activity_selector(_RaisingClient(), "1", "999")
        self.assertIsNone(result)


class TestProcessZipPart(unittest.TestCase):
    def _write_zip(self, entries):
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        with zipfile.ZipFile(tmp.name, "w") as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        self.addCleanup(os.unlink, tmp.name)
        return Path(tmp.name)

    def test_skips_invalid_fit_entry_without_crashing_and_processes_the_rest(self):
        zip_path = self._write_zip({
            "kevin.klaes@gmail.com_111.fit": b"not a real fit file",
            "kevin.klaes@gmail.com_222.fit": b"placeholder bytes -- extract_records is patched below",
        })

        good_record = {
            'timestamp': datetime.datetime(2021, 6, 1, 8, 0, 0),
            'core_temperature': 36.9,
        }

        def fake_extract_records(fit_bytes):
            if fit_bytes.startswith(b"not a real fit file"):
                raise bct.FitParseError("Invalid .FIT File Header")
            return [good_record]

        written = []
        totals = {"activities_scanned": 0, "activities_with_core_data": 0, "points_written": 0, "entries_skipped": 0}

        with patch.object(bct, "extract_records", side_effect=fake_extract_records), \
             patch.object(bct, "lookup_activity_selector", return_value="20210601T080000UTC-running"):
            bct.process_zip_part(
                zip_path,
                influx_client=None,
                influxdb_version="1",
                device="TestDevice",
                database="TestDB",
                write_points=written.append,
                dry_run=False,
                totals=totals,
            )

        self.assertEqual(totals["activities_scanned"], 2)
        self.assertEqual(totals["entries_skipped"], 1)
        self.assertEqual(totals["activities_with_core_data"], 1)
        self.assertEqual(totals["points_written"], 1)
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0][0]["tags"]["ActivityID"], "222")
        self.assertEqual(written[0][0]["fields"], {"CoreTemperature": 36.9})

    def test_dry_run_does_not_call_write_points(self):
        zip_path = self._write_zip({"kevin.klaes@gmail.com_333.fit": b"placeholder"})
        good_record = {'timestamp': datetime.datetime(2021, 1, 1), 'core_temperature': 36.5}
        totals = {"activities_scanned": 0, "activities_with_core_data": 0, "points_written": 0, "entries_skipped": 0}

        with patch.object(bct, "extract_records", return_value=[good_record]), \
             patch.object(bct, "lookup_activity_selector", return_value="20210101T000000UTC-running"):
            bct.process_zip_part(
                zip_path,
                influx_client=None,
                influxdb_version="1",
                device="TestDevice",
                database="TestDB",
                write_points=lambda points: self.fail("write_points should not be called in dry-run"),
                dry_run=True,
                totals=totals,
            )

        self.assertEqual(totals["points_written"], 1)

    def test_skips_activity_with_no_existing_series_to_backfill_onto(self):
        zip_path = self._write_zip({"kevin.klaes@gmail.com_444.fit": b"placeholder"})
        good_record = {'timestamp': datetime.datetime(2021, 1, 1), 'core_temperature': 36.5}
        totals = {"activities_scanned": 0, "activities_with_core_data": 0, "points_written": 0, "entries_skipped": 0}

        with patch.object(bct, "extract_records", return_value=[good_record]), \
             patch.object(bct, "lookup_activity_selector", return_value=None):
            bct.process_zip_part(
                zip_path,
                influx_client=None,
                influxdb_version="1",
                device="TestDevice",
                database="TestDB",
                write_points=lambda points: self.fail("write_points should not be called"),
                dry_run=False,
                totals=totals,
            )

        self.assertEqual(totals["entries_skipped"], 1)
        self.assertEqual(totals["activities_with_core_data"], 0)


if __name__ == "__main__":
    unittest.main()
