### CORE Body-Temperature Sensor Backfill (from a local Garmin export)

`src/garmin_grafana/backfill_core_temperature.py` (gg-k97) backfills CORE
by greenTEG sensor fields — `CoreTemperature`, `SkinTemperature`,
`HeatStrainIndex`, `CoreDataQuality` — into historical activities. Live
ingestion (gg-m1u) only covers activities synced *after* that code landed;
this script fills in everything before that, using the raw FIT files
already sitting in your Garmin Connect data export.

Runs entirely offline against the export — no Garmin Connect login, no API
calls, no rate limits. It only **adds** fields to already-existing
`ActivityGPS` points (matched by `ActivityID` + timestamp); it never
touches or requires re-syncing activities from scratch, and skips any
activity that was never live-fetched into InfluxDB in the first place
(nothing to attach the new fields to).

#### Prerequisites

- A full Garmin Connect data export (GDPR takeout) containing
  `DI_CONNECT/DI-Connect-Uploaded-Files/*.zip` — these zips hold the raw
  per-activity `.fit` files the script reads. (Requesting this export from
  Garmin can take a while — see
  [Export All Garmin Data](https://support.garmin.com/en-US/?faq=W1TvTPW8JZ6LfJSfK512Q8).)
- The activities you care about must already exist in InfluxDB's
  `ActivityGPS` measurement (i.e. they've been fetched at least once by the
  normal garmin-fetch-data container) — the script backfills fields onto
  existing points, it doesn't create new activities.
- Because it writes to InfluxDB, it needs network access to wherever that
  runs — on training-mini this means running it **on training-mini itself**
  (or via an SSH tunnel to its InfluxDB port; running directly on the box
  is simpler).

#### Running it on training-mini

1. Get the export onto training-mini. Only the `DI-Connect-Uploaded-Files`
   subfolder is actually needed (the rest of a GDPR export is unrelated to
   this):

   ```bash
   rsync -az ~/Documents/garmintakeout/DI_CONNECT/DI-Connect-Uploaded-Files \
     training-mini:~/garmin-export-DI_CONNECT/
   # (mkdir -p ~/garmin-export-DI_CONNECT/DI_CONNECT on the Mini first if needed,
   #  or adjust the destination path so the script's expected layout —
   #  <export-dir>/DI_CONNECT/DI-Connect-Uploaded-Files — is preserved)
   ```

2. Dry-run first — this reports what *would* be written without touching
   InfluxDB, and is the fast way to sanity-check the export made it over
   correctly:

   ```bash
   ssh training-mini
   cd ~/homelab/garmin-grafana
   docker compose --env-file .env -f deploy/training-mini-compose.yml run --rm \
     garmin-fetch-data python garmin_grafana/backfill_core_temperature.py \
     --export-dir /path/to/garmin-export-DI_CONNECT/.. --dry-run
   ```

   (`--export-dir` should point at the directory that directly *contains*
   `DI_CONNECT/`, matching the same layout `garmin_bulk_importer.py`
   expects — see manual-import-instructions.md above. You'll likely need a
   `-v <host-path>:/bulk_export` volume mount on the `docker compose run`
   command, same as the bulk importer, so the container can see the files;
   then point `--export-dir` at `/bulk_export`.)

   Watch the log output: `N activities scanned`, `N with core data`,
   `N points written`, `N skipped`. Every activity you actually wore the
   CORE sensor for should show up in the "with core data" count.

3. If the dry-run numbers look right, run for real (drop `--dry-run`):

   ```bash
   docker compose --env-file .env -f deploy/training-mini-compose.yml run --rm \
     garmin-fetch-data python garmin_grafana/backfill_core_temperature.py \
     --export-dir /bulk_export
   ```

   Safe to re-run if interrupted — InfluxDB writes here are idempotent
   (same measurement/tags/timestamp/fields just overwrite themselves).

4. Verify in Grafana — pick an activity you know had the CORE sensor paired
   on the Activity Detail dashboard and confirm the new fields show real
   values, not blanks.

Full technical rationale (why it looks up the existing `ActivitySelector`
tag instead of re-deriving it from the FIT file, InfluxDB's partial-field
merge behavior, etc.) is in the script's own module docstring —
`src/garmin_grafana/backfill_core_temperature.py`.
