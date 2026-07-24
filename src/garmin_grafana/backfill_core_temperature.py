"""Backfill CORE body-temperature sensor fields into historical ActivityGPS points.

Historical activities predate this project's CORE sensor support (see
core_sensor.py) and never had CoreTemperature/SkinTemperature/HeatStrainIndex/
CoreDataQuality written to InfluxDB. This script re-reads each activity's
original FIT file from a local Garmin Connect data export (GDPR takeout, e.g.
~/Documents/garmintakeout) and writes just those fields to the *existing*
ActivityGPS point for that activity+timestamp. InfluxDB merges new fields into
an existing point that shares the same measurement, tag set, and timestamp
rather than overwriting it -- confirmed for both InfluxDB 1.x and 3.x (v3
core docs: "if you initially write a point with field A, you can later write
another point with the same tags and timestamp containing field B, and
InfluxDB [...] will merge them into a single row").

Runs entirely offline against the export -- no garminconnect API calls, no
login, no rate limits.

The ActivitySelector tag is looked up from the already-stored ActivityGPS
point for each activity ID rather than re-derived from the FIT file, because
the FIT session's `sport` field (e.g. "cycling") doesn't reliably reproduce
the Garmin Connect activity typeKey used to build the original tag (e.g.
"road_biking") -- guessing wrong would put the new fields in a different
series that the per-activity dashboard (which filters on an exact
ActivitySelector tag match) would never query, silently making the backfill
invisible.

Usage:
    python backfill_core_temperature.py --export-dir ~/Documents/garmintakeout
    python backfill_core_temperature.py --dry-run
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import re
import zipfile
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace

from fitparse import FitFile, FitParseError

try:
    from garmin_grafana import core_sensor
except ImportError:  # run as a plain script (python garmin_grafana/backfill_core_temperature.py)
    import core_sensor

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# The activity ID is the digit run immediately after the email prefix. Anchoring on
# "@" (rather than the end of the string) is required because some exported filenames
# carry extra "_tap-sync-<n>-<hash>" suffix data after the activity ID, e.g.
# "kevin.klaes@gmail.com_1002111179.fit" -> "1002111179"
# "kevin.klaes@gmail.com_5983748945_tap-sync-16984-3c381e52af6f985ffd5f11889891870c.fit" -> "5983748945"
FIT_FILENAME_RE = re.compile(r"@[^_]*_(\d+)", re.IGNORECASE)


def activity_id_from_filename(filename: str) -> str | None:
    """Extract the numeric Garmin activity ID from an export filename, or None if it doesn't match."""
    match = FIT_FILENAME_RE.search(filename)
    return match.group(1) if match else None


def core_sensor_present(parsed_record: dict) -> bool:
    return any(v is not None for v in core_sensor.core_sensor_fields(parsed_record).values())


def core_temperature_point(parsed_record: dict, *, activity_id: str, activity_selector: str,
                            device: str, database: str) -> dict | None:
    """Build a partial-field ActivityGPS point with only the CORE sensor fields.

    Returns None if the record has no timestamp or no CORE sensor data at all
    (most historical records won't -- CORE sensor pairing is a recent addition).
    """
    timestamp = parsed_record.get('timestamp')
    if timestamp is None:
        return None
    fields = {k: v for k, v in core_sensor.core_sensor_fields(parsed_record).items() if v is not None}
    if not fields:
        return None
    return {
        "measurement": "ActivityGPS",
        "time": timestamp.replace(tzinfo=timezone.utc).isoformat(),
        "tags": {
            "Device": device,
            "Database_Name": database,
            "ActivityID": activity_id,
            "ActivitySelector": activity_selector,
        },
        "fields": fields,
    }


def lookup_activity_selector(influx_client, influxdb_version: str, activity_id: str) -> str | None:
    """Look up the ActivitySelector tag already stored for this activity's ActivityGPS points.

    Returns None if no existing ActivityGPS point is found for this ID (e.g.
    an activity that was never live-fetched into InfluxDB) -- there's nothing
    to backfill onto.
    """
    # activity_id is always digits-only (see FIT_FILENAME_RE), so this is safe to inline.
    query = f'SELECT * FROM "ActivityGPS" WHERE "ActivityID" = \'{activity_id}\' LIMIT 1'
    try:
        if influxdb_version == '1':
            points = list(influx_client.query(query).get_points())
        else:
            points = influx_client.query(query=query, language="influxql").to_pylist()
        return points[0]['ActivitySelector'] if points else None
    except Exception as err:
        logger.warning("ActivitySelector lookup failed for ActivityID %s: %s", activity_id, err)
        return None


def extract_records(fit_bytes: bytes) -> list:
    """Parse a FIT file's record messages. Raises FitParseError on invalid data."""
    fitfile = FitFile(io.BytesIO(fit_bytes))
    fitfile.parse()
    return [record.get_values() for record in fitfile.get_messages('record')]


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
                records = extract_records(zf.read(name))
            except (FitParseError, zipfile.BadZipFile, OSError, EOFError) as err:
                logger.warning("Skipping %s in %s: not a valid FIT file (%s)", name, zip_path.name, err)
                totals["entries_skipped"] += 1
                continue

            records_with_core_data = [
                r for r in records if r.get('timestamp') is not None and core_sensor_present(r)
            ]
            if not records_with_core_data:
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
                    core_temperature_point(
                        r, activity_id=activity_id, activity_selector=activity_selector,
                        device=device, database=database,
                    )
                    for r in records_with_core_data
                ) if point is not None
            ]
            if not points:
                continue

            totals["activities_with_core_data"] += 1
            totals["points_written"] += len(points)
            if dry_run:
                logger.info("[dry-run] Would write %d CORE sensor points for ActivityID %s", len(points), activity_id)
            else:
                write_points(points)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="Garmin CORE Sensor Backfill",
        description=(
            "Backfills CORE body-temperature sensor fields (core/skin temperature, "
            "heat strain) into historical ActivityGPS points from a local Garmin "
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
        "activities_with_core_data": 0,
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
            "Progress: %d/%d zip parts processed | %d activities scanned | %d with core data | "
            "%d points written | %d skipped",
            i, len(zip_paths), totals["activities_scanned"], totals["activities_with_core_data"],
            totals["points_written"], totals["entries_skipped"],
        )

    logger.info(
        "Done: %d zip parts | %d activities scanned | %d activities with core data | "
        "%d points written | %d skipped",
        len(zip_paths), totals["activities_scanned"], totals["activities_with_core_data"],
        totals["points_written"], totals["entries_skipped"],
    )


if __name__ == "__main__":
    main()
