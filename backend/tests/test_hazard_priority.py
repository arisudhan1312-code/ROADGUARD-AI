import unittest

from hazard_priority import calculate_hazard_priority, sort_priority_key
from hazard_priority_service import present_priority, recalculate_location_priorities


class HazardPriorityTests(unittest.TestCase):
    def test_scoring_returns_explained_levels_and_critical_safety_override(self):
        low = calculate_hazard_priority("Low", 1, "Unknown", "Other")
        medium = calculate_hazard_priority("Low", 1, "Unknown", "Pothole")
        high = calculate_hazard_priority("High", 1, "Unknown", "Pothole")
        critical = calculate_hazard_priority(
            "High", 1, "Unknown", "Waterlogging"
        )

        self.assertEqual(low["priority"], "Low")
        self.assertEqual(medium["priority"], "Medium")
        self.assertEqual(high["priority"], "High")
        self.assertEqual(critical["priority"], "Critical")
        self.assertIn("Critical safety override", critical["explanation"])
        self.assertEqual(sum(high["components"].values()), high["score"])

    def test_unassessed_severity_is_not_fabricated(self):
        result = calculate_hazard_priority(None, 1, "Unknown", "Other")

        self.assertFalse(result["severity_assessed"])
        self.assertEqual(result["components"]["severity"], 0)
        self.assertIn("severity not assessed yet", result["explanation"])

    def test_admin_override_remains_distinct_from_calculated_recommendation(self):
        report = {
            "hazard_category": "Other",
            "severity": "Low",
            "priority_report_count": 1,
            "traffic_importance": "Unknown",
            "priority_source": "Admin Override",
            "hazard_priority": "Critical",
        }

        priority = present_priority(report)

        self.assertEqual(priority["calculated_priority"], "Low")
        self.assertEqual(priority["hazard_priority"], "Critical")
        self.assertIn("Administrator override", priority["priority_explanation"])

    def test_sorting_places_priority_before_distance(self):
        hazards = [
            {"priority": "Low", "score": 26, "distance": 10},
            {"priority": "Critical", "score": 62, "distance": 400},
            {"priority": "High", "score": 55, "distance": 5},
        ]

        hazards.sort(
            key=lambda hazard: sort_priority_key(
                hazard["priority"],
                hazard["score"],
                hazard["distance"],
            )
        )

        self.assertEqual(
            [hazard["priority"] for hazard in hazards],
            ["Critical", "High", "Low"],
        )

    def test_recalculation_uses_one_count_for_all_matching_reports(self):
        class Cursor:
            def __init__(self):
                self.updates = []
                self.rows = [
                    {
                        "report_id": "RG-1",
                        "hazard_category": "Pothole",
                        "traffic_importance": "Unknown",
                        "priority_source": "Rule-Based",
                        "hazard_priority": None,
                        "severity": "Low",
                    },
                    {
                        "report_id": "RG-2",
                        "hazard_category": "Other",
                        "traffic_importance": "High",
                        "priority_source": "Rule-Based",
                        "hazard_priority": None,
                        "severity": "Medium",
                    },
                ]

            def execute(self, query, parameters):
                if query.lstrip().startswith("UPDATE"):
                    self.updates.append(parameters)

            def fetchall(self):
                return self.rows

        cursor = Cursor()
        results = recalculate_location_priorities(cursor, "Main Road")

        self.assertEqual(set(results), {"RG-1", "RG-2"})
        self.assertEqual(len(cursor.updates), 2)
        self.assertEqual(cursor.updates[0][3], 2)
        self.assertEqual(cursor.updates[1][3], 2)


if __name__ == "__main__":
    unittest.main()
