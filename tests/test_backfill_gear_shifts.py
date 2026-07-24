"""Unit tests for backfill_gear_shifts (Di2/ANT+ gear-shift historical backfill script).

Run from the repo root with:
    python3 -m unittest discover tests
"""
import datetime
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from garmin_grafana import backfill_gear_shifts as bgs


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
            "kevin.klaes@gmail.com_222.fit": b"placeholder bytes -- extract_events is patched below",
        })

        good_event = {
            'event': 'rear_gear_change',
            'timestamp': datetime.datetime(2021, 6, 1, 8, 0, 0),
            'rear_gear': 21,
            'rear_gear_num': 3,
        }

        def fake_extract_events(fit_bytes):
            if fit_bytes.startswith(b"not a real fit file"):
                raise bgs.FitParseError("Invalid .FIT File Header")
            return [good_event]

        written = []
        totals = {"activities_scanned": 0, "activities_with_gear_shifts": 0, "points_written": 0, "entries_skipped": 0}

        with patch.object(bgs, "extract_events", side_effect=fake_extract_events), \
             patch.object(bgs, "lookup_activity_selector", return_value="20210601T080000UTC-road_biking"):
            bgs.process_zip_part(
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
        self.assertEqual(totals["activities_with_gear_shifts"], 1)
        self.assertEqual(totals["points_written"], 1)
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0][0]["tags"]["ActivityID"], "222")
        self.assertEqual(written[0][0]["fields"], {
            "FrontGear": None, "FrontGearNum": None, "RearGear": 21, "RearGearNum": 3,
        })

    def test_skips_activities_with_no_gear_change_events(self):
        zip_path = self._write_zip({"kevin.klaes@gmail.com_333.fit": b"placeholder"})
        non_gear_event = {'event': 'timer', 'timestamp': datetime.datetime(2021, 1, 1)}
        totals = {"activities_scanned": 0, "activities_with_gear_shifts": 0, "points_written": 0, "entries_skipped": 0}

        with patch.object(bgs, "extract_events", return_value=[non_gear_event]), \
             patch.object(bgs, "lookup_activity_selector", return_value="20210101T000000UTC-road_biking"):
            bgs.process_zip_part(
                zip_path,
                influx_client=None,
                influxdb_version="1",
                device="TestDevice",
                database="TestDB",
                write_points=lambda points: self.fail("write_points should not be called"),
                dry_run=False,
                totals=totals,
            )

        self.assertEqual(totals["activities_with_gear_shifts"], 0)
        self.assertEqual(totals["points_written"], 0)

    def test_dry_run_does_not_call_write_points(self):
        zip_path = self._write_zip({"kevin.klaes@gmail.com_444.fit": b"placeholder"})
        good_event = {
            'event': 'front_gear_change', 'timestamp': datetime.datetime(2021, 1, 1),
            'front_gear': 54, 'front_gear_num': 2,
        }
        totals = {"activities_scanned": 0, "activities_with_gear_shifts": 0, "points_written": 0, "entries_skipped": 0}

        with patch.object(bgs, "extract_events", return_value=[good_event]), \
             patch.object(bgs, "lookup_activity_selector", return_value="20210101T000000UTC-road_biking"):
            bgs.process_zip_part(
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
        zip_path = self._write_zip({"kevin.klaes@gmail.com_555.fit": b"placeholder"})
        good_event = {
            'event': 'rear_gear_change', 'timestamp': datetime.datetime(2021, 1, 1),
            'rear_gear': 21, 'rear_gear_num': 3,
        }
        totals = {"activities_scanned": 0, "activities_with_gear_shifts": 0, "points_written": 0, "entries_skipped": 0}

        with patch.object(bgs, "extract_events", return_value=[good_event]), \
             patch.object(bgs, "lookup_activity_selector", return_value=None):
            bgs.process_zip_part(
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
        self.assertEqual(totals["activities_with_gear_shifts"], 0)


if __name__ == "__main__":
    unittest.main()
