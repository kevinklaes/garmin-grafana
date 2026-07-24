"""Backfill Di2/ANT+ electronic shifting gear-change events into historical activities.

Historical activities predate this project's gear-shift event support (see
gear_shift.py) and never had GearShiftEvents points written to InfluxDB. This
script re-reads each activity's original FIT file from a local Garmin Connect
data export (GDPR takeout, e.g. ~/Documents/garmintakeout) and writes new
GearShiftEvents points -- one per front_gear_change/rear_gear_change FIT event
message. Unlike the CORE sensor backfill (which merges fields into existing
ActivityGPS points), GearShiftEvents is its own measurement with its own
timestamps, so this always writes brand-new points rather than merging fields
into an existing series.

Runs entirely offline against the export -- no garminconnect API calls, no
login, no rate limits.

The ActivitySelector tag is looked up from the already-stored ActivityGPS
point for each activity ID rather than re-derived from the FIT file, because
the FIT session's `sport` field (e.g. "cycling") doesn't reliably reproduce
the Garmin Connect activity typeKey used to build the original tag (e.g.
"road_biking") -- guessing wrong would put the new points in a different
series that the per-activity dashboard (which filters on an exact
ActivitySelector tag match) would never query, silently making the backfill
invisible. Non-cycling activities have no gear-change events at all -- that's
expected, not an error, and such activities are simply skipped.

Usage:
    python backfill_gear_shifts.py --export-dir ~/Documents/garmintakeout
    python backfill_gear_shifts.py --dry-run
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

from fitparse import FitFile, FitParseError

try:
    from garmin_grafana import gear_shift
    from garmin_grafana.backfill_core_temperature import activity_id_from_filename, lookup_activity_selector
except ImportError:  # run as a plain script (python garmin_grafana/backfill_gear_shifts.py)
    import gear_shift
    from backfill_core_temperature import activity_id_from_filename, lookup_activity_selector

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def extract_events(fit_bytes: bytes) -> list:
    """Parse a FIT file's event messages. Raises FitParseError on invalid data."""
    fitfile = FitFile(io.BytesIO(fit_bytes))
    fitfile.parse()
    return [event.get_values() for event in fitfile.get_messages('event')]


def process_zip_part(zip_path: Path, *, influx_client, influxdb_version: str, device: str,
                      database: str, write_points, dry_run: bool, totals: dict) -> None:
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as err:
        logger.warning("Skipping export zip %s: failed to open (%s)", zip_path.name, err)
        return
    with zf:
        for name in zf.namelist():
            if not name.lower().endswith(".fit"):
                continue
            totals["activities_scanned"] += 1
            activity_id = activity_id_from_filename(name)
            if activity_id is None:
                logger.warning("Skipping %s in %s: filename doesn't match <email>_<activityID>.fit", name, zip_path.name)
                totals["entries_skipped"] += 1
                continue
            try:
                events = extract_events(zf.read(name))
            except (FitParseError, zipfile.BadZipFile, OSError, EOFError) as err:
                logger.warning("Skipping %s in %s: not a valid FIT file (%s)", name, zip_path.name, err)
                totals["entries_skipped"] += 1
                continue

            gear_change_events = [e for e in events if gear_shift.gear_shift_event_fields(e) is not None]
            if not gear_change_events:
                continue

            activity_selector = lookup_activity_selector(influx_client, influxdb_version, activity_id)
            if activity_selector is None:
                logger.warning(
                    "Skipping ActivityID %s: no existing ActivityGPS series in InfluxDB to backfill onto",
                    activity_id,
                )
                totals["entries_skipped"] += 1
                continue

            points = [
                point for point in (
                    gear_shift.gear_shift_point(
                        e, activity_id=activity_id, activity_selector=activity_selector,
                        device=device, database=database,
                    )
                    for e in gear_change_events
                ) if point is not None
            ]
            if not points:
                continue

            totals["activities_with_gear_shifts"] += 1
            totals["points_written"] += len(points)
            if dry_run:
                logger.info("[dry-run] Would write %d GearShiftEvents points for ActivityID %s", len(points), activity_id)
            else:
                write_points(points)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="Garmin Gear Shift Backfill",
        description=(
            "Backfills Di2/ANT+ electronic shifting gear-change events "
            "(GearShiftEvents) into historical activities from a local Garmin "
            "Connect data export."
        ),
    )
    parser.add_argument(
        "--export-dir",
        default=os.getenv("GARMIN_TAKEOUT_EXPORT_DIR", "~/Documents/garmintakeout"),
        help="Path to the Garmin Connect data export (GDPR takeout) root directory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report what would be written without writing to InfluxDB.",
    )
    args = parser.parse_args(argv)

    export_dir = Path(os.path.expanduser(args.export_dir))
    uploaded_files_dir = export_dir / "DI_CONNECT" / "DI-Connect-Uploaded-Files"
    if not uploaded_files_dir.is_dir():
        raise FileNotFoundError(f"Expected export directory not found: {uploaded_files_dir}")

    try:
        from garmin_grafana import garmin_fetch
    except ImportError:
        import garmin_fetch

    if garmin_fetch.TAG_MEASUREMENTS_WITH_USER_EMAIL:
        # write_points_to_influxdb reads garmin_obj.display_name; we never log in to
        # Garmin Connect, so stand in with the configured email (fit_activity_importer.py
        # uses the same "stub garmin_obj" approach for headless writes).
        garmin_fetch.garmin_obj = SimpleNamespace(display_name=garmin_fetch.GARMINCONNECT_EMAIL or 'Unknown')

    zip_paths = sorted(uploaded_files_dir.glob("*.zip"))
    logger.info("Found %d export zip parts in %s", len(zip_paths), uploaded_files_dir)

    totals = {
        "activities_scanned": 0,
        "activities_with_gear_shifts": 0,
        "points_written": 0,
        "entries_skipped": 0,
    }

    for i, zip_path in enumerate(zip_paths, start=1):
        process_zip_part(
            zip_path,
            influx_client=garmin_fetch.influxdbclient,
            influxdb_version=garmin_fetch.INFLUXDB_VERSION,
            device=garmin_fetch.GARMIN_DEVICENAME,
            database=garmin_fetch.INFLUXDB_DATABASE,
            write_points=garmin_fetch.write_points_to_influxdb,
            dry_run=args.dry_run,
            totals=totals,
        )
        logger.info(
            "Progress: %d/%d zip parts processed | %d activities scanned | %d with gear shifts | "
            "%d points written | %d skipped",
            i, len(zip_paths), totals["activities_scanned"], totals["activities_with_gear_shifts"],
            totals["points_written"], totals["entries_skipped"],
        )

    logger.info(
        "Done: %d zip parts | %d activities scanned | %d activities with gear shifts | "
        "%d points written | %d skipped",
        len(zip_paths), totals["activities_scanned"], totals["activities_with_gear_shifts"],
        totals["points_written"], totals["entries_skipped"],
    )


if __name__ == "__main__":
    main()
