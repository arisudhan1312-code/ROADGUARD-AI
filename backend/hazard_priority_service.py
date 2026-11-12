"""Database helpers shared by report administration and driver hazard APIs."""

from hazard_priority import (
    build_priority_explanation,
    calculate_hazard_priority,
)


def present_priority(report, report_count=None):
    """Return a live, explained priority view without changing report status."""
    stored_count = report.get("priority_report_count")
    current_count = report_count or stored_count or 1
    traffic_importance = report.get("traffic_importance") or "Unknown"
    result = calculate_hazard_priority(
        report.get("severity"),
        int(current_count),
        traffic_importance,
        report.get("hazard_category") or report["category"],
    )

    priority_source = report.get("priority_source") or "Rule-Based"
    effective_priority = result["priority"]
    if priority_source == "Admin Override" and report.get("hazard_priority"):
        effective_priority = report["hazard_priority"]
    return {
        "hazard_priority": effective_priority,
        "calculated_priority": result["priority"],
        "priority_score": result["score"],
        "priority_report_count": int(current_count),
        "traffic_importance": traffic_importance,
        "priority_source": priority_source,
        "priority_explanation": build_priority_explanation(
            result,
            priority_source,
            effective_priority,
        ),
        "priority_components": result["components"],
        "severity_assessed": result["severity_assessed"],
    }


def recalculate_location_priorities(cursor, location_name):
    """Persist scores for every non-rejected report at a normalized location."""
    cursor.execute(
        """
        SELECT r.report_id, r.hazard_category, r.traffic_importance,
               r.priority_source, r.hazard_priority, h.severity
        FROM road_reports AS r
        LEFT JOIN hazards AS h ON h.report_id = r.report_id
        WHERE LOWER(TRIM(r.location_name)) = LOWER(TRIM(%s))
          AND r.status <> 'Rejected'
        ORDER BY r.report_datetime, r.report_id
        FOR UPDATE
        """,
        (location_name,),
    )
    reports = cursor.fetchall()
    report_count = len(reports)
    if not report_count:
        return {}

    results = {}
    for report in reports:
        priority_view = present_priority(report, report_count)
        cursor.execute(
            """
            UPDATE road_reports
            SET calculated_priority = %s,
                hazard_priority = %s,
                priority_score = %s,
                priority_report_count = %s
            WHERE report_id = %s
            """,
            (
                priority_view["calculated_priority"],
                priority_view["hazard_priority"],
                priority_view["priority_score"],
                report_count,
                report["report_id"],
            ),
        )
        results[report["report_id"]] = priority_view
    return results
