"""Unit tests for core_sensor (CORE body-temperature FIT record fields).

Run from the repo root with:
    python3 -m unittest discover tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from garmin_grafana.core_sensor import core_sensor_fields


class TestCoreSensorFields(unittest.TestCase):
    def test_present_fields_are_extracted(self):
        parsed_record = {
            'timestamp': object(),
            'core_temperature': 37.0,
            'skin_temperature': 33.15,
            'heat_strain_index': 0.0,
            'core_data_quality': 20,
        }
        fields = core_sensor_fields(parsed_record)
        self.assertEqual(fields, {
            "CoreTemperature": 37.0,
            "SkinTemperature": 33.15,
            "HeatStrainIndex": 0.0,
            "CoreDataQuality": 20,
        })

    def test_missing_fields_are_none(self):
        parsed_record = {'timestamp': object(), 'heart_rate': 140}
        fields = core_sensor_fields(parsed_record)
        self.assertEqual(fields, {
            "CoreTemperature": None,
            "SkinTemperature": None,
            "HeatStrainIndex": None,
            "CoreDataQuality": None,
        })

    def test_ciq_prefixed_fallback_used_when_plain_field_absent(self):
        parsed_record = {
            'timestamp': object(),
            'CIQ_core_temperature': 36.8,
            'CIQ_skin_temperature': 32.9,
        }
        fields = core_sensor_fields(parsed_record)
        self.assertEqual(fields["CoreTemperature"], 36.8)
        self.assertEqual(fields["SkinTemperature"], 32.9)

    def test_plain_field_preferred_over_ciq_duplicate(self):
        parsed_record = {
            'timestamp': object(),
            'core_temperature': 37.0,
            'CIQ_core_temperature': 36.8,
        }
        fields = core_sensor_fields(parsed_record)
        self.assertEqual(fields["CoreTemperature"], 37.0)


if __name__ == "__main__":
    unittest.main()
