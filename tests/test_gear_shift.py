"""Unit tests for gear_shift (Di2/ANT+ electronic shifting FIT event fields).

Run from the repo root with:
    python3 -m unittest discover tests
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from garmin_grafana.gear_shift import gear_shift_event_fields, gear_shift_point


class TestGearShiftEventFields(unittest.TestCase):
    def test_rear_gear_change_event_fields(self):
        parsed_event = {
            'event': 'rear_gear_change',
            'timestamp': datetime.datetime(2024, 1, 1, 12, 0, 0),
            'rear_gear': 21,
            'rear_gear_num': 3,
            'gear_change_data': 123456,
        }
        fields = gear_shift_event_fields(parsed_event)
        self.assertEqual(fields, {
            "FrontGear": None,
            "FrontGearNum": None,
            "RearGear": 21,
            "RearGearNum": 3,
        })

    def test_front_gear_change_event_fields(self):
        parsed_event = {
            'event': 'front_gear_change',
            'timestamp': datetime.datetime(2024, 1, 1, 12, 0, 0),
            'front_gear': 54,
            'front_gear_num': 2,
        }
        fields = gear_shift_event_fields(parsed_event)
        self.assertEqual(fields, {
            "FrontGear": 54,
            "FrontGearNum": 2,
            "RearGear": None,
            "RearGearNum": None,
        })

    def test_non_gear_change_event_returns_none(self):
        parsed_event = {'event': 'timer', 'timestamp': datetime.datetime(2024, 1, 1)}
        self.assertIsNone(gear_shift_event_fields(parsed_event))

    def test_missing_event_key_returns_none(self):
        self.assertIsNone(gear_shift_event_fields({'timestamp': datetime.datetime(2024, 1, 1)}))


class TestGearShiftPoint(unittest.TestCase):
    def test_builds_point_for_gear_change_event(self):
        parsed_event = {
            'event': 'rear_gear_change',
            'timestamp': datetime.datetime(2024, 1, 1, 12, 0, 0),
            'rear_gear': 21,
            'rear_gear_num': 3,
        }
        point = gear_shift_point(
            parsed_event, activity_id="123", activity_selector="20240101T120000UTC-road_biking",
            device="TestDevice", database="TestDB",
        )
        self.assertEqual(point["measurement"], "GearShiftEvents")
        self.assertEqual(point["time"], "2024-01-01T12:00:00+00:00")
        self.assertEqual(point["tags"], {
            "Device": "TestDevice",
            "Database_Name": "TestDB",
            "ActivityID": "123",
            "ActivitySelector": "20240101T120000UTC-road_biking",
            "EventType": "rear_gear_change",
        })
        self.assertEqual(point["fields"], {
            "FrontGear": None,
            "FrontGearNum": None,
            "RearGear": 21,
            "RearGearNum": 3,
        })

    def test_returns_none_for_non_gear_change_event(self):
        parsed_event = {'event': 'timer', 'timestamp': datetime.datetime(2024, 1, 1)}
        result = gear_shift_point(
            parsed_event, activity_id="1", activity_selector="x", device="d", database="db",
        )
        self.assertIsNone(result)

    def test_returns_none_for_gear_change_event_without_timestamp(self):
        parsed_event = {'event': 'front_gear_change', 'front_gear': 54}
        result = gear_shift_point(
            parsed_event, activity_id="1", activity_selector="x", device="d", database="db",
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
