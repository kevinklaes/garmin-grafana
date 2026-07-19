#!/usr/bin/env python3
"""Build Grafana_Dashboard/Garmin-Intraday-Day.json from the main dashboard export.

Keeps intraday-focused panels (through Activity Duration plus day heatmaps),
sets a single-day default time range, and adds a Day query variable for quick
navigation. Run from repo root after editing Garmin-Grafana-Dashboard.json.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "Grafana_Dashboard" / "Garmin-Grafana-Dashboard.json"
OUT = REPO / "Grafana_Dashboard" / "Garmin-Intraday-Day.json"

LONG_TERM_ROW = "Long term visualization"
EXTRA_INTRADAY = {
    "HR Histogram Heatmap",
    "Hourly walk heatmap",
    "Sleep Regularity",
    "Battery Level (Requires GarminHomeAssistant from ConnectIQ integration)",
}
RENAME = {
    "Today RHR": "Resting Heart Rate",
    "Today's Steps": "Steps",
    "Today SpO2": "SpO2",
    "Today Activity": "Activities",
}


def main() -> None:
    dash = json.loads(SRC.read_text())
    panels = dash["panels"]
    long_term_y = next(
        p["gridPos"]["y"] for p in panels if p.get("title") == LONG_TERM_ROW
    )
    kept = []
    for panel in panels:
        title = panel.get("title", "")
        y = panel["gridPos"]["y"]
        if title == LONG_TERM_ROW:
            break
        if y < long_term_y or title in EXTRA_INTRADAY:
            kept.append(copy.deepcopy(panel))
    for panel in panels:
        if panel.get("title") in EXTRA_INTRADAY and panel not in kept:
            kept.append(copy.deepcopy(panel))

    for panel in kept:
        if panel.get("title") in RENAME:
            panel["title"] = RENAME[panel["title"]]

    out = copy.deepcopy(dash)
    out["panels"] = kept
    out.pop("id", None)
    out.pop("version", None)
    out["uid"] = "garmin-intraday-day"
    out["title"] = "Garmin Intraday (Day View)"
    out["description"] = (
        "Single-day intraday stats. Pick a date with the time picker (top right) "
        "or the Day dropdown. Uses Local Time Zone for day boundaries."
    )
    out["tags"] = ["garmin", "intraday", "day-view"]
    out["refresh"] = ""
    out["time"] = {"from": "now/d", "to": "now"}
    out["timepicker"] = {
        "refresh_intervals": [],
        "quick_ranges": [
            {"display": "Today", "from": "now/d", "to": "now"},
            {"display": "Yesterday", "from": "now-1d/d", "to": "now-1d/d"},
        ],
    }
    out["links"] = [
        {
            "asDropdown": False,
            "icon": "external link",
            "includeVars": True,
            "keepTime": True,
            "tags": [],
            "targetBlank": False,
            "title": "Garmin Stats (Intraday)",
            "tooltip": "Today and recent context",
            "type": "link",
            "url": "/d/garmin-grafana-dashboard/garmin-stats",
        },
        {
            "asDropdown": False,
            "icon": "external link",
            "includeVars": False,
            "keepTime": True,
            "tags": [],
            "targetBlank": False,
            "title": "Activity Detail",
            "tooltip": "Per-activity GPS maps and detail graphs",
            "type": "link",
            "url": "/d/garmin-activity-detail/garmin-activity-detail",
        },
        {
            "asDropdown": False,
            "icon": "external link",
            "includeVars": False,
            "keepTime": False,
            "tags": [],
            "targetBlank": False,
            "title": "Long-Term Health",
            "tooltip": "Weekly and monthly trends",
            "type": "link",
            "url": "/d/garmin-long-term-health/garmin-long-term-health",
        },
    ]

    templating = out.get("templating", {"list": []})
    tz_list = templating.get("list", [])
    for opt in tz_list:
        if opt.get("name") == "TimeZone":
            for choice in opt.get("options", []):
                choice["selected"] = choice.get("value") == "America/Denver"
            opt["current"] = {"text": "America/Denver", "value": "America/Denver"}
            break
    day_var = {
        "current": {"selected": True, "text": "Today", "value": "now/d"},
        "datasource": {"type": "influxdb", "uid": "${DS_GARMIN_STATS}"},
        "definition": "",
        "description": "Jump to a day with data (updates time range)",
        "hide": 0,
        "includeAll": False,
        "label": "Day",
        "multi": False,
        "name": "Day",
        "options": [],
        "query": {
            "query": 'SELECT last("HeartRate") FROM "HeartRateIntraday" WHERE time > now() - 400d GROUP BY time(1d) tz(\'$TimeZone\') fill(none)',
            "refId": "InfluxVariableQueryEditor-VariableQuery",
        },
        "refresh": 2,
        "regex": "/.*time.*(\\d{4}-\\d{2}-\\d{2}).*/",
        "skipUrlSync": False,
        "sort": 2,
        "type": "query",
    }
    if not any(v.get("name") == "Day" for v in tz_list):
        tz_list.insert(0, day_var)
    out["templating"] = {"list": tz_list}

    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(f"Wrote {OUT} ({len(kept)} panels)")


if __name__ == "__main__":
    main()
