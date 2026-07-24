"""CORE by greenTEG body-temperature sensor fields (core temp, skin temp, heat strain).

Some FIT record messages carry per-second core body temperature sensor data
when a CORE sensor was paired and synced during the activity. Field names on
FIT record messages sometimes appear duplicated with a ``CIQ_`` prefix
(identical values observed in sample files); the plain field is preferred,
falling back to the CIQ-prefixed one when only that is populated.

Pure (no I/O); ``garmin_fetch.py`` wires this into ActivityGPS per-record
point building.
"""
from __future__ import annotations


def core_sensor_fields(parsed_record: dict) -> dict:
    return {
        "CoreTemperature": parsed_record.get('core_temperature', None) or parsed_record.get('CIQ_core_temperature', None),
        "SkinTemperature": parsed_record.get('skin_temperature', None) or parsed_record.get('CIQ_skin_temperature', None),
        "HeatStrainIndex": parsed_record.get('heat_strain_index', None),
        "CoreDataQuality": parsed_record.get('core_data_quality', None),
    }
