"""Unit tests for hr_backfill (activity HR -> HeartRateIntraday gap backfill).

Run from the repo root with:
    python3 -m unittest discover tests
"""
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from garmin_grafana.hr_backfill import (
    BACKFILL_TAG,
    BACKFILL_VALUE,
    HrSample,
    activity_hr_samples_from_points,
    backfill_points_to_influx_format,
    bucket_activity_hr,
    plan_hr_backfill,
    watch_epochs_from_points,
    watch_hr_near,
)

BASE = 1_760_000_000  # arbitrary fixed epoch (UTC), keeps tests deterministic


def iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


class TestWatchHrNear(unittest.TestCase):
    def test_empty_watch_series_is_never_near(self):
        self.assertFalse(watch_hr_near([], BASE, 90))

    def test_within_gap_before_and_after(self):
        watch = [BASE - 60, BASE + 500]
        self.assertTrue(watch_hr_near(watch, BASE, 90))
        self.assertTrue(watch_hr_near(watch, BASE + 420, 90))

    def test_outside_gap(self):
        watch = [BASE]
        self.assertFalse(watch_hr_near(watch, BASE + 91, 90))
        self.assertTrue(watch_hr_near(watch, BASE + 90, 90))


class TestBucketActivityHr(unittest.TestCase):
    def test_mean_per_bucket_and_alignment(self):
        aligned_base = BASE // 60 * 60
        samples = [
            HrSample(aligned_base + 1, 100),
            HrSample(aligned_base + 30, 110),
            HrSample(aligned_base + 59, 120),
            HrSample(aligned_base + 61, 140),
        ]
        buckets = bucket_activity_hr(samples, 60)
        self.assertEqual(len(buckets), 2)
        self.assertEqual(buckets[0].epoch, aligned_base)
        self.assertAlmostEqual(buckets[0].heart_rate, 110.0)
        self.assertAlmostEqual(buckets[1].heart_rate, 140.0)

    def test_activity_id_propagates_to_bucket(self):
        samples = [HrSample(BASE, 100, activity_id="12345")]
        buckets = bucket_activity_hr(samples, 60)
        self.assertEqual(buckets[0].activity_id, "12345")

    def test_rejects_invalid_resolution(self):
        with self.assertRaises(ValueError):
            bucket_activity_hr([], 0)


class TestPlanHrBackfill(unittest.TestCase):
    def test_skips_buckets_near_watch_samples(self):
        # Watch samples every 10 min over 30 min; activity HR at 1 Hz throughout.
        # Buckets whose start is within 90s of a watch sample must be skipped.
        aligned_base = BASE // 600 * 600
        watch = [aligned_base + offset for offset in (0, 600, 1200, 1800)]
        activity = [HrSample(aligned_base + i, 130) for i in range(1800)]
        planned = plan_hr_backfill(watch, activity, gap_seconds=90, resolution=60)
        # 30 buckets total; starts within 90s of watch: {0,60}, {540,600,660}, {1140,1200,1260}, {1740} -> 9 skipped
        self.assertEqual(len(planned), 21)
        planned_offsets = {p.epoch - aligned_base for p in planned}
        for skipped in (0, 60, 540, 600, 660, 1140, 1200, 1260, 1740):
            self.assertNotIn(skipped, planned_offsets)

    def test_no_watch_data_backfills_everything(self):
        activity = [HrSample(BASE // 60 * 60 + i, 120) for i in range(0, 300, 10)]
        planned = plan_hr_backfill([], activity, gap_seconds=90, resolution=60)
        self.assertEqual(len(planned), 5)

    def test_skips_existing_backfill_epochs(self):
        aligned_base = BASE // 60 * 60
        activity = [HrSample(aligned_base, 120), HrSample(aligned_base + 60, 125)]
        planned = plan_hr_backfill([], activity, gap_seconds=90, resolution=60,
                                   existing_backfill_epochs={aligned_base})
        self.assertEqual([p.epoch for p in planned], [aligned_base + 60])

    def test_min_max_hr_filter(self):
        aligned_base = BASE // 60 * 60
        activity = [
            HrSample(aligned_base, 20),        # below min: dropped
            HrSample(aligned_base + 60, 240),  # above max: dropped
            HrSample(aligned_base + 120, 150), # kept
        ]
        planned = plan_hr_backfill([], activity, gap_seconds=90, resolution=60)
        self.assertEqual(len(planned), 1)
        self.assertAlmostEqual(planned[0].heart_rate, 150.0)


class TestInfluxFormat(unittest.TestCase):
    def test_point_shape_tags_and_integer_field(self):
        aligned_base = BASE // 60 * 60
        planned = plan_hr_backfill([], [HrSample(aligned_base, 132.4, activity_id="987")],
                                   gap_seconds=90, resolution=60)
        points = backfill_points_to_influx_format(planned, "Edge 840", "GarminStats")
        self.assertEqual(len(points), 1)
        point = points[0]
        self.assertEqual(point["measurement"], "HeartRateIntraday")
        self.assertEqual(point["time"], iso(aligned_base))
        self.assertEqual(point["tags"]["Device"], "Edge 840")
        self.assertEqual(point["tags"]["Database_Name"], "GarminStats")
        self.assertEqual(point["tags"][BACKFILL_TAG], BACKFILL_VALUE)
        self.assertEqual(point["tags"]["ActivityID"], "987")
        self.assertEqual(point["fields"]["HeartRate"], 132)
        self.assertIsInstance(point["fields"]["HeartRate"], int)

    def test_activity_id_tag_omitted_when_unknown(self):
        points = backfill_points_to_influx_format(
            plan_hr_backfill([], [HrSample(BASE, 100)], gap_seconds=90, resolution=60),
            "Watch", "GarminStats")
        self.assertNotIn("ActivityID", points[0]["tags"])


class TestPointConverters(unittest.TestCase):
    def test_watch_epochs_from_points_ignores_backfilled(self):
        points = [
            {"measurement": "HeartRateIntraday", "time": iso(BASE),
             "tags": {"Device": "W"}, "fields": {"HeartRate": 70}},
            {"measurement": "HeartRateIntraday", "time": iso(BASE + 60),
             "tags": {"Device": "W", BACKFILL_TAG: BACKFILL_VALUE}, "fields": {"HeartRate": 71}},
            {"measurement": "StepsIntraday", "time": iso(BASE + 120),
             "tags": {}, "fields": {"StepsCount": 5}},
        ]
        self.assertEqual(watch_epochs_from_points(points), [BASE])

    def test_activity_samples_from_points_skips_missing_hr_and_other_measurements(self):
        points = [
            {"measurement": "ActivityGPS", "time": iso(BASE),
             "tags": {"ActivityID": "42"}, "fields": {"HeartRate": 140.0}},
            {"measurement": "ActivityGPS", "time": iso(BASE + 1),
             "tags": {"ActivityID": "42"}, "fields": {"HeartRate": None}},
            {"measurement": "ActivitySession", "time": iso(BASE + 2),
             "tags": {"ActivityID": "42"}, "fields": {"HeartRate": 150.0}},
        ]
        samples = activity_hr_samples_from_points(points)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].epoch, BASE)
        self.assertEqual(samples[0].heart_rate, 140.0)
        self.assertEqual(samples[0].activity_id, "42")

    def test_naive_timestamps_treated_as_utc(self):
        # TCX fallback in fetch_activity_GPS produces naive UTC timestamps
        naive = datetime.fromtimestamp(BASE, tz=timezone.utc).replace(tzinfo=None).isoformat()
        points = [{"measurement": "ActivityGPS", "time": naive,
                   "tags": {}, "fields": {"HeartRate": 120.0}}]
        self.assertEqual(activity_hr_samples_from_points(points)[0].epoch, BASE)


if __name__ == "__main__":
    unittest.main()
