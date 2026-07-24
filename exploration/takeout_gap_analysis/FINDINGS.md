# Garmin Data Takeout — Gap Analysis Findings

Research/exploration only (gg-3uo). No importers were built; this is the
recommendation set for follow-up beads. Every claim below was checked against
the actual takeout files at `~/Documents/garmintakeout/DI_CONNECT` and
`DI_TACX` using [`inventory_takeout.py`](./inventory_takeout.py) (schema/type
inventory only — no field values were extracted or committed) and against
what `src/garmin_grafana/garmin_fetch.py` currently ingests, plus the
`garminconnect==0.2.8` API wrapper's actual method list (checked by
downloading and inspecting the package — not assumed from memory).

Personal data (activity/device/gear IDs, bike/shoe names, real health
values) was viewed transiently in an interactive shell while writing this
report but was never written to any file in this repo. Only field names,
JSON types, and non-identifying counts/categories (e.g. record counts,
enum-like labels such as "Bike"/"Shoes") appear below.

## How to reproduce this inventory

```bash
python3 exploration/takeout_gap_analysis/inventory_takeout.py ~/Documents/garmintakeout/DI_CONNECT --samples 5
python3 exploration/takeout_gap_analysis/inventory_takeout.py ~/Documents/garmintakeout/DI_TACX --samples 5
```

Output is field-path + JSON-type only (e.g. `[].gearTypeName: str`) — safe to
read, paste, or extend without redaction. Add `--group <substring>` to filter
to one schema family.

## Availability legend

- **Live (dedicated)** — a dedicated method exists in the `garminconnect`
  Python wrapper already used by this project. Lowest-effort path to a
  real importer.
- **Live (undocumented)** — no dedicated wrapper method, but the takeout
  filename/JSON shape suggests a Garmin Connect service endpoint that could
  be reached via the wrapper's generic `connectapi()` escape hatch (already
  used in `get_lactate_threshold`). Unconfirmed without live testing;
  higher effort and more fragile (undocumented REST paths can change).
- **Historical-only** — this data exists in the GDPR export but the live
  API path returns only a current snapshot, not the full history. Only
  recoverable from a one-time takeout import, never from ongoing sync.

## Confirmed gaps (from the bead's hypothesis) — verified against real files

### 1. Personal Records — `DI-Connect-Fitness/*_personalRecord.json`
**Priority: High.** 86 records in the export, but only 16 have `current: true`
— the other 70 are superseded PRs (`current: false`) with their own
`prStartTimeGMT` and `createdDate`. Categories are stable labels like
"Best 5km Run", "Farthest Cycle", "Max Avg Power (20 min)", "Most Steps in a
Day" (16 distinct categories observed).
- **Live availability:** `garminconnect.Garmin.get_personal_record()` exists
  and is not currently called anywhere in `garmin_fetch.py`. It almost
  certainly returns only the *current* record per category (Garmin's UI
  shows one current PR each), matching the 16 `current: true` rows.
- **Gap within the gap:** the **70 historical/superseded PR events are
  Historical-only** — nothing in the live API wrapper surfaces PR history,
  only the live-current value. If PR *progression over time* is wanted on a
  dashboard, it must come from this one-time export; going forward, only the
  current record per category would be captured.
- **Recommendation:** build a live importer for current PRs (cheap, `feat`),
  and consider a one-time backfill of the historical PR timeline from this
  takeout (separate, deliberate one-off script, run locally, output written
  to InfluxDB directly — never committed to the repo).

### 2. Gear — `DI-Connect-Fitness/*_gear.json`
**Priority: High.** 22 gear items (`gearTypeName` ∈ {Bike, Shoes, Other},
`gearStatusName` ∈ {active, retired}), each with `dateBegin`/`dateEnd`,
`maximumMeters` (retirement threshold), and a `gearActivityDTOs` map keyed by
gear ID to the list of activity IDs it was used on.
- **Live availability:** `get_gear(userProfileNumber)`, `get_gear_stats(gearUUID)`,
  `get_gear_defaults(userProfileNumber)`, and `get_activity_gear(activity_id)`
  all exist in the wrapper and are unused today.
- **Value:** this is the most commonly requested feature in this class of
  dashboard (shoe/bike mileage tracking, retirement countdown). Fully
  live-syncable — no historical-only component.
- **Recommendation:** high-value, build a live importer. Consider a `Gear`
  measurement (one point per gear item, updated on each run) plus tagging
  `ActivitySummary`/`StrengthExerciseSet` points with the gear used via
  `get_activity_gear`.

### 3. Abnormal HR Events — `DI-Connect-Wellness/*_AbnormalHrEvents.json`
**Priority: Low.** Only 16 events across the account's full history (3
files). Fields: `abnormalHrEventGMT`, `abnormalHrThresholdValue`,
`abnormalHrValue`, `calendarDate`, `deviceId`.
- **Live availability:** no dedicated wrapper method found.
  **Live (undocumented)** at best — would need a raw `connectapi()` call to
  an unconfirmed wellness-service endpoint.
- **Recommendation:** low priority given the very low event rate; not worth
  the reverse-engineering effort unless Kevin specifically wants HR-anomaly
  alerting.

### 4. Cycling Ability score — `DI-Connect-Metrics/CyclingAbility_*.json`
**Priority: Medium.** Distinct from per-activity Cycling Dynamics (pedaling
metrics) already ingested. Fields: `aerobicCapacity`/`aerobicEndurance`/
`anaerobicCapacity` (scores) plus matching `*Feedback` text fields,
`profileType`/`profileTypeFeedback`. Data starts **2025-12-02** — this is a
recently introduced Garmin feature for this account/device (before that,
zero files), with dense records (151–298 per ~3-month window, i.e. roughly
daily or more).
- **Live availability:** no dedicated wrapper method. **Live (undocumented)**
  — likely a `metrics-service` endpoint analogous to `get_endurance_score`/
  `get_hill_score`, which use similar per-date-range REST calls already in
  this codebase as a pattern to copy.
- **Recommendation:** medium priority — genuinely new signal (cycling-specific
  fitness score), but needs endpoint discovery since it's recent enough that
  the `garminconnect` wrapper hasn't added it yet.

### 5. Workouts / Training Plans — `DI-Connect-Fitness/*_workout.json`, `*_trainingPlan.json`
**Priority: Low.** `trainingPlan.json` is an **empty list** — Kevin has never
had an adaptive/coach training plan active. `workout.json` has real content
(`workoutList` with structured step/target/condition trees, `workoutScheduleList`).
- **Live availability:** no dedicated wrapper method (`get_workouts` does not
  exist in 0.2.8). **Live (undocumented)** at best.
- **Recommendation:** low priority — this is workout *authoring* config, not
  a health/fitness metric time series. Not a natural fit for a Grafana trends
  dashboard. Skip unless Kevin wants a "workout library" panel specifically.

### 6. Courses — `DI-Connect-Routing/*_courses_*.json`
**Priority: Low.** Route library with GPS points, elevation profile, course
points (turns/POIs). 3 files/courses in the export.
- **Live availability:** no dedicated wrapper method. **Live (undocumented)**.
- **Recommendation:** low priority — this is route-planning data, not a
  time-series metric. Only interesting if building a "planned vs actual route"
  overlay, which is a much bigger feature than this bead's scope.

### 7. Power Guidance — `DI-Connect-Routing/*_powerguidances_*.json`
**Priority: Low.** Cycling pacing plans tied to a course (target power per
segment, rider/bike mass, aero coefficients). 2 files.
- **Live availability:** no dedicated wrapper method. **Live (undocumented)**,
  and tightly coupled to the Courses feature above (same low-priority
  reasoning — pre-activity planning data, not a post-hoc metric).

## Additional gaps found in the file contents (not anticipated from filenames)

These weren't in the bead's initial list — they surfaced from actually
opening the files, mostly under `DI-Connect-Aggregator/UDSFile` and
`DI-Connect-Metrics`, which turned out to be far richer than their filenames
suggested.

### 8. All-day stress breakdown + Body Battery event log
**Priority: High.** `UDSFile` (the daily "User Daily Summary" backup) embeds
`allDayStress.aggregatorList` (per-type duration breakdown: rest/low/medium/
high/activity/uncategorized stress seconds, counts, "off-wrist" seconds) and
`bodyBatteryFeedback.bodyBatteryActivityEventList` (discrete body-battery
impact events — each with an impact score, event type, duration, and short/
long feedback codes explaining *why* body battery moved). Neither of these
is captured today: `get_daily_stats()` only pulls `DailyStats` summary totals
(`stressDuration`, `bodyBatteryChargedValue`, etc.), not the per-type
breakdown or the discrete events.
- **Live availability:** `garminconnect.Garmin.get_all_day_stress(cdate)` is
  a dedicated wrapper method that is **not currently called anywhere** in
  `garmin_fetch.py`. Needs live verification that it returns this same
  `allDayStress`/`bodyBatteryFeedback` shape (very likely, since UDSFile is
  Garmin's own backup of daily wellness aggregates), but this is the
  cheapest new gap to close — the method already exists and is unused.
- **Recommendation:** high value (explains *why* body battery/stress moved,
  which today's dashboard can't show), low effort (call an already-available
  wrapper method).

### 9. Heat & Altitude Acclimation — `DI-Connect-Metrics/MetricsHeatAltitudeAcclimation`
**Priority: Medium.** `heatAcclimationPercentage`, `altitudeAcclimation`,
`acclimationPercentage`, `currentAltitude`/`prevAltitude`, trend labels
(`altitudeTrend`, `heatTrend`). 23 files (roughly one per training-status
sync period), not ingested at all today.
- **Live availability:** no dedicated wrapper method found, but this is very
  likely bundled into the same response as `get_training_status()` (Garmin's
  training-status endpoint commonly nests a heat/altitude acclimation DTO
  alongside training load) — **Live (undocumented)**, worth checking the raw
  `get_training_status()` payload before assuming a new endpoint is needed.
- **Recommendation:** medium priority for endurance/altitude training
  context; check the existing `get_training_status()` response shape first
  since the wrapper call is already made.

### 10. Training History trend fields — `DI-Connect-Metrics/TrainingHistory`
**Priority: Low (enhancement, not a new integration).** Same underlying data
source as the already-ingested `TrainingStatus` measurement
(`get_training_status()` / `mostRecentTrainingStatus.latestTrainingStatusData`),
but the takeout shows additional fields not currently extracted:
`loadTunnelMin`/`loadTunnelMax` (recommended training load range),
`fitnessLevelTrend`, `loadLevelTrend`, `trainingStatus2FeedbackPhrase`.
- **Live availability:** Live (dedicated) — already fetched, just not all
  fields are read out of the response.
- **Recommendation:** trivial addition to `get_training_status()`'s
  `data_fields` dict — no new API call needed.

### 11. Goals — `DI-Connect-User/UserGoal_*.json`
**Priority: Medium.** The bead flagged this as "not yet assessed." Fields:
`userGoalCategory`, `userGoalType`, `goalValue`, `activityTypePk`, date range.
Only 6 files, most from 2018–2019 with one recent one (2023) — Kevin rarely
sets goals in the Connect app.
- **Live availability:** `garminconnect.Garmin.get_goals(status, start, limit)`
  is a dedicated, unused wrapper method.
- **Recommendation:** medium priority given low historical usage — cheap to
  wire up if Kevin starts using Goals again, but not urgent.

### 12. `sleepData` / `TrainingReadinessDTO` extra fields
**Priority: Low (enhancement).** Both already-ingested measurements have a
few extra fields visible in the takeout but not in `garmin_fetch.py`'s
`data_fields` dicts: sleep has `napList` (nap start/end/duration — Kevin's
device supports nap detection but no `NapSummary`/`SleepIntraday` nap points
exist today) and per-component `sleepScores` breakdown (`deepScore`,
`remScore`, `recoveryScore`, etc. — today only `sleepScores.overall.value` is
stored). Training readiness has `inputContext` and `recoveryTimeChangePhrase`
text fields, not currently stored.
- **Live availability:** Live (dedicated) — same calls already made
  (`get_sleep_data`, `get_training_readiness`); just add fields.
- **Recommendation:** cheap follow-up: extract nap events and per-component
  sleep scores from the response already being fetched.

## Reviewed and found NOT to be gaps

- **Lifestyle Journal** (`DI-Connect-Wellness/LifestyleLogging`) — the
  takeout uses different top-level key names (`dailyLogList` vs. the live
  API's `dailyLogsReport`) for the *same* feature already ingested by
  `get_lifestyle_data()`. Not a gap — just a naming difference between the
  export format and the live API response.
- **Hydration** (`DI-Connect-Aggregator/HydrationLogFile`, and the
  `UDSFile.hydration` sub-object) — `get_hydration()` already captures
  `valueInML`, `sweatLossInML`, `goalInML`, `activityIntakeInML`. The takeout
  adds only `adjustedGoalInML` and a local timestamp — minor, not worth a
  separate importer.
- **VO2 Max / Fitness Age / Endurance Score / Hill Score / Training
  Readiness / Race Predictions / Acute Training Load** (`DI-Connect-Metrics/
  ActivityVo2Max`, `MetricsMaxMetData`, `EnduranceScore`, `HillScore`,
  `TrainingReadinessDTO`, `RunRacePredictions`, `MetricsAcuteTrainingLoad`) —
  all already ingested live via their respective `get_*` calls. The takeout
  versions are Garmin's own backup of the exact same data, confirming these
  integrations are complete (module minor unread fields noted in #10/#12).
- **`summarizedActivitiesExport`** (`DI-Connect-Fitness/USER_summarizedActivities`)
  — a bulk backup of the same fields already captured per-activity via
  `get_activity_summary()` / FIT file parsing. Not a new gap; would only
  matter as an alternate backfill source if the live activity history API
  had gaps, which isn't indicated here.
- **Health Status baseline data, Manual Stress Level, Nutrition Logs,
  heart-rate/power zone settings, `userBioMetrics(ProfileData)`,
  `bioMetrics_latest`, `wellnessActivities`** — all low-frequency
  (1–3 files each), mostly device/profile *configuration* snapshots rather
  than a health time series, or (Manual Stress Level, Nutrition Logs)
  reflect rare manual user actions. Not worth an importer at this time.
- **Device Backups, User profile/contact/settings/reminders/social-profile,
  Social comments/likes** — identity, device-config, or social-network data,
  not fitness metrics. Explicitly out of scope for a health dashboard, and
  ingesting profile/contact data would itself be a privacy regression.
- **Golf clubs** (`DI-GOLF/*`) — present in the export but Kevin doesn't play
  golf per the bead context; skip.
- **Tacx bike profiles / subscriptions / workouts export** — subscriptions
  and workouts files are empty or near-empty; bike profile data
  (weight, tire circumference, aero coefficients) is static config, not a
  metric, and only matters if the existing Cycling Dynamics power-phase
  ingestion needs rider/bike mass context (it doesn't currently use it).

## Priority summary

| Gap | Priority | Live path | Effort |
|---|---|---|---|
| All-day stress breakdown + Body Battery events (#8) | High | `get_all_day_stress()` — dedicated, unused | Low |
| Gear tracking (#2) | High | `get_gear*`, `get_activity_gear()` — dedicated, unused | Low–Medium |
| Personal Records — current (#1) | High | `get_personal_record()` — dedicated, unused | Low |
| Personal Records — history (#1) | High value, one-time | Historical-only (takeout) | One-off script |
| Cycling Ability score (#4) | Medium | Undocumented endpoint | Medium |
| Heat/Altitude Acclimation (#9) | Medium | Possibly bundled in `get_training_status()` | Low–Medium |
| Goals (#11) | Medium | `get_goals()` — dedicated, unused | Low |
| TrainingHistory extra fields (#10) | Low (enhancement) | Already-fetched response | Trivial |
| Sleep naps / score breakdown (#12) | Low (enhancement) | Already-fetched response | Trivial |
| Abnormal HR Events (#3) | Low | Undocumented endpoint | Medium, low value |
| Workouts/Training Plans (#5) | Low | Undocumented endpoint | Not a metric series |
| Courses (#6) | Low | Undocumented endpoint | Not a metric series |
| Power Guidance (#7) | Low | Undocumented endpoint | Not a metric series |

These are recommendations for Kevin to prioritize — no importers were built
as part of this bead. Follow-up beads should be filed per gap he chooses to
pursue.
