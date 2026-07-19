"""Backfill HeartRateIntraday gaps from ActivityGPS heart-rate samples.

When heart rate is recorded by a secondary device during an activity —
typically a bike computer — the samples are stored under
``ActivityGPS.HeartRate`` but never reach the watch-based
``HeartRateIntraday`` stream. Intraday Grafana panels query
``HeartRateIntraday`` only, so those periods show up as gaps even though
per-activity HR exists.

This module plans supplemental ``HeartRateIntraday`` points from activity HR:
activity samples are averaged into fixed-width buckets, and a bucket becomes a
backfill point only when no organic watch sample exists within
``gap_seconds`` of it. Supplemental points are tagged
``BackfillSource=ActivityGPS`` (plus ``ActivityID`` when known) so they can be
audited or deleted without touching organic watch data.

All functions are pure (no I/O); ``garmin_fetch.py`` wires them into the
daily sync when ``BACKFILL_HR_FROM_ACTIVITIES`` is enabled. Re-running a day
is idempotent: identical points share the same series and timestamp, so
InfluxDB overwrites them in place.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

BACKFILL_TAG = "BackfillSource"
BACKFILL_VALUE = "ActivityGPS"


@dataclass(frozen=True)
class HrSample:
    epoch: float  # unix seconds (UTC)
    heart_rate: float
    activity_id: str | None = None


@dataclass(frozen=True)
class BackfillPoint:
    epoch: int  # bucket start, unix seconds (UTC)
    heart_rate: float  # bucket mean; rounded to int when formatted for InfluxDB
    activity_id: str | None = None


def _iso_to_epoch(iso_time):
    dt = datetime.fromisoformat(str(iso_time).replace("Z", "+00:00"))
    if dt.tzinfo is None:  # TCX fallback records naive UTC timestamps
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def watch_epochs_from_points(points):
    """Extract sorted epoch seconds from HeartRateIntraday point dicts."""
    epochs = [
        _iso_to_epoch(point["time"])
        for point in points
        if point.get("measurement") == "HeartRateIntraday"
        and point.get("tags", {}).get(BACKFILL_TAG) != BACKFILL_VALUE
    ]
    return sorted(epochs)


def activity_hr_samples_from_points(points):
    """Extract HrSample list from ActivityGPS point dicts (skips records without HR)."""
    samples = []
    for point in points:
        if point.get("measurement") != "ActivityGPS":
            continue
        heart_rate = point.get("fields", {}).get("HeartRate")
        if not heart_rate:
            continue
        samples.append(HrSample(
            epoch=_iso_to_epoch(point["time"]),
            heart_rate=float(heart_rate),
            activity_id=point.get("tags", {}).get("ActivityID"),
        ))
    return sorted(samples, key=lambda sample: sample.epoch)


def watch_hr_near(watch_epochs, epoch, gap_seconds):
    """Return True if any watch sample lies within gap_seconds of epoch (watch_epochs must be sorted)."""
    if not watch_epochs:
        return False
    index = bisect_left(watch_epochs, epoch)
    for neighbor in (index - 1, index):
        if 0 <= neighbor < len(watch_epochs) and abs(watch_epochs[neighbor] - epoch) <= gap_seconds:
            return True
    return False


def bucket_activity_hr(samples, resolution_seconds):
    """Average activity HR samples into fixed-width buckets, keyed by bucket start."""
    if resolution_seconds < 1:
        raise ValueError("resolution_seconds must be >= 1")
    buckets = defaultdict(list)
    activity_by_bucket = {}
    for sample in samples:
        bucket = int(sample.epoch) // resolution_seconds * resolution_seconds
        buckets[bucket].append(sample.heart_rate)
        if sample.activity_id and bucket not in activity_by_bucket:
            activity_by_bucket[bucket] = sample.activity_id
    return [
        BackfillPoint(
            epoch=bucket_start,
            heart_rate=sum(values) / len(values),
            activity_id=activity_by_bucket.get(bucket_start),
        )
        for bucket_start, values in sorted(buckets.items())
    ]


def plan_hr_backfill(watch_epochs, activity_hr_samples, *, gap_seconds=90, resolution=60,
                     min_hr=35, max_hr=220, existing_backfill_epochs=frozenset()):
    """Plan supplemental HeartRateIntraday points for gaps in watch coverage.

    watch_epochs must be sorted ascending. Activity samples outside
    [min_hr, max_hr] are discarded before bucketing. Buckets whose start lies
    within gap_seconds of a watch sample, or is already present in
    existing_backfill_epochs, are skipped.
    """
    filtered = [s for s in activity_hr_samples if min_hr <= s.heart_rate <= max_hr]
    planned = []
    for point in bucket_activity_hr(filtered, resolution):
        if point.epoch in existing_backfill_epochs:
            continue
        if watch_hr_near(watch_epochs, point.epoch, gap_seconds):
            continue
        planned.append(point)
    return planned


def backfill_points_to_influx_format(points, device, database):
    """Convert BackfillPoints to the point-dict shape used by write_points_to_influxdb.

    HeartRate is written as an integer to match the series type of organic
    HeartRateIntraday watch data in InfluxDB 1.x.
    """
    influx_points = []
    for point in points:
        tags = {
            "Device": device,
            "Database_Name": database,
            BACKFILL_TAG: BACKFILL_VALUE,
        }
        if point.activity_id:
            tags["ActivityID"] = point.activity_id
        influx_points.append({
            "measurement": "HeartRateIntraday",
            "time": datetime.fromtimestamp(point.epoch, tz=timezone.utc).isoformat(),
            "tags": tags,
            "fields": {"HeartRate": int(round(point.heart_rate))},
        })
    return influx_points
