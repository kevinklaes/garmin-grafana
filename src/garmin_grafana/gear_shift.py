"""Electronic shifting (Di2 / ANT+) gear-change fields from FIT event messages.

FIT event messages fire on actual derailleur shifts -- sparse, irregular event
data (unlike CORE sensor's continuous per-second record stream), so this gets
its own GearShiftEvents measurement rather than being folded into ActivityGPS.
A front_gear_change event only carries front_gear/front_gear_num; a
rear_gear_change event only carries rear_gear/rear_gear_num -- the other pair
is left None rather than assumed present.

The FIT event message also carries a `gear_change_data` field (a raw uint32
with no enum/scale decoding in the FIT profile). It is intentionally omitted
here: the FIT SDK profile gives no documented meaning for it, and it appears
to be a bit-packed re-encoding of the same front/rear gear values already
exposed as separate, already-decoded fields -- not worth guessing at.

Pure (no I/O); garmin_fetch.py wires this into per-activity FIT processing,
and backfill_gear_shifts.py reuses it for historical activities.
"""
from __future__ import annotations

from datetime import timezone

GEAR_CHANGE_EVENTS = {"front_gear_change", "rear_gear_change"}


def gear_shift_event_fields(parsed_event: dict) -> dict | None:
    """Field values for a gear-change FIT event message.

    Returns None if the event isn't a front_gear_change/rear_gear_change event.
    """
    if parsed_event.get('event') not in GEAR_CHANGE_EVENTS:
        return None
    return {
        "FrontGear": parsed_event.get('front_gear'),
        "FrontGearNum": parsed_event.get('front_gear_num'),
        "RearGear": parsed_event.get('rear_gear'),
        "RearGearNum": parsed_event.get('rear_gear_num'),
    }


def gear_shift_point(parsed_event: dict, *, activity_id, activity_selector: str,
                      device: str, database: str) -> dict | None:
    """Build a GearShiftEvents point for a FIT event message, or None if not applicable.

    Returns None if the event isn't a gear-change event, or it has no timestamp.
    FIT timestamps are naive UTC; this attaches tzinfo (a no-op if the caller
    already made it timezone-aware).
    """
    fields = gear_shift_event_fields(parsed_event)
    if fields is None:
        return None
    timestamp = parsed_event.get('timestamp')
    if timestamp is None:
        return None
    return {
        "measurement": "GearShiftEvents",
        "time": timestamp.replace(tzinfo=timezone.utc).isoformat(),
        "tags": {
            "Device": device,
            "Database_Name": database,
            "ActivityID": activity_id,
            "ActivitySelector": activity_selector,
            "EventType": parsed_event['event'],
        },
        "fields": fields,
    }
