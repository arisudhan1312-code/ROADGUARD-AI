"""Transparent, rule-based priority scoring for road hazard reports."""

PRIORITY_ORDER = {
    "Critical": 0,
    "High": 1,
    "Medium": 2,
    "Low": 3,
}

VALID_PRIORITIES = frozenset(PRIORITY_ORDER)
VALID_TRAFFIC_IMPORTANCE = frozenset({"Unknown", "Low", "Medium", "High"})

SEVERITY_POINTS = {
    "High": 30,
    "Medium": 20,
    "Low": 10,
}

TRAFFIC_POINTS = {
    "Unknown": 0,
    "Low": 5,
    "Medium": 12,
    "High": 20,
}

HAZARD_POINTS = {
    "Pothole": 16,
    "Damaged Road": 12,
    "Broken Streetlight": 14,
    "Waterlogging": 18,
    "Road Obstruction": 20,
    "Other": 8,
}

ROAD_USER_EXPOSURE_POINTS = {
    "Pothole": 13,
    "Damaged Road": 12,
    "Broken Streetlight": 14,
    "Waterlogging": 14,
    "Road Obstruction": 15,
    "Other": 8,
}


def _report_count_points(report_count):
    if report_count >= 5:
        return 15
    if report_count >= 3:
        return 10
    if report_count >= 2:
        return 5
    return 0


def calculate_hazard_priority(
    severity,
    report_count,
    traffic_importance,
    hazard_category,
):
    """Return a score, priority, and factor-by-factor explanation.

    ``report_count`` includes the current report and counts non-rejected
    submissions whose normalized location name matches.
    """
    if isinstance(report_count, bool) or not isinstance(report_count, int) or report_count < 1:
        raise ValueError("report_count must be a positive integer.")
    if severity is not None and (
        not isinstance(severity, str) or severity not in SEVERITY_POINTS
    ):
        raise ValueError("severity must be High, Medium, Low, or None.")
    if (
        not isinstance(traffic_importance, str)
        or traffic_importance not in VALID_TRAFFIC_IMPORTANCE
    ):
        raise ValueError("traffic_importance must be Unknown, Low, Medium, or High.")
    if not isinstance(hazard_category, str) or hazard_category not in HAZARD_POINTS:
        raise ValueError("hazard_category is not supported.")

    severity_points = SEVERITY_POINTS.get(severity, 0)
    repeat_points = _report_count_points(report_count)
    traffic_points = TRAFFIC_POINTS[traffic_importance]
    hazard_points = HAZARD_POINTS[hazard_category]
    exposure_points = ROAD_USER_EXPOSURE_POINTS[hazard_category]
    score = severity_points + repeat_points + traffic_points + hazard_points + exposure_points

    immediate_risk = severity == "High" and hazard_category in {
        "Waterlogging",
        "Road Obstruction",
    }
    if immediate_risk or score >= 75:
        priority = "Critical"
    elif score >= 55:
        priority = "High"
    elif score >= 35:
        priority = "Medium"
    else:
        priority = "Low"

    severity_reason = (
        f"severity {severity} +{severity_points}"
        if severity
        else "severity not assessed yet +0"
    )
    location_reason = f"{report_count} report(s) at this named location +{repeat_points}"
    explanation = (
        f"Rule score {score}/100: {severity_reason}; {location_reason}; "
        f"traffic +{traffic_points}; {hazard_category} type +{hazard_points}; "
        f"pedestrian/driver exposure +{exposure_points}."
    )
    if immediate_risk:
        explanation += " Critical safety override: High-severity waterlogging or road obstruction."
    elif score >= 75:
        explanation += " Critical score threshold reached."

    return {
        "score": score,
        "priority": priority,
        "explanation": explanation,
        "components": {
            "severity": severity_points,
            "repeat_reports": repeat_points,
            "traffic_importance": traffic_points,
            "hazard_type": hazard_points,
            "road_user_exposure": exposure_points,
        },
        "report_count": report_count,
        "traffic_importance": traffic_importance,
        "severity_assessed": severity is not None,
    }


def sort_priority_key(priority, score=0, distance_meters=0):
    """Sort highest priority first, then by score and distance."""
    return (
        PRIORITY_ORDER.get(priority, len(PRIORITY_ORDER)),
        -int(score or 0),
        float(distance_meters or 0),
    )


def build_priority_explanation(result, priority_source, effective_priority=None):
    """Explain manual overrides without hiding the rule-based recommendation."""
    if priority_source == "Admin Override" and effective_priority:
        return (
            f"{result['explanation']} Administrator override: effective priority "
            f"{effective_priority}; calculated recommendation was {result['priority']}."
        )
    return result["explanation"]
