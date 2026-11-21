"""
Deterministic post-generation validator for Traffo.
Cross-checks LLM triage outputs against verified live telemetry (TomTom + Open-Meteo)
and incident severity cues before any report is returned to the caller.
"""

import re
from typing import Optional

from schemas import IncidentReport

CONGESTION_RANK = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}

_LANES_BLOCKED_RE = re.compile(r"\b(\d+)\s+lanes?\s+(?:are\s+)?blocked\b", re.IGNORECASE)
_ALL_LANES_RE = re.compile(r"\ball\s+lanes\b", re.IGNORECASE)
_PEAK_HOUR_RE = re.compile(
    r"\b(?:7am|8am|9am|5pm|6pm|7pm|8pm|peak|rush(?:\s+hour)?)\b",
    re.IGNORECASE,
)


def extract_lanes_blocked(text: str) -> Optional[int]:
    """Extract numeric blocked-lane count from incident text if explicitly stated."""
    match = _LANES_BLOCKED_RE.search(text or "")
    if match:
        return int(match.group(1))
    return None


def is_peak_hour(text: str) -> bool:
    """Return True when incident text indicates peak/rush-hour travel conditions."""
    return bool(_PEAK_HOUR_RE.search(text or ""))


def validate_report(report: IncidentReport, telemetry: dict, incident_text: str) -> dict:
    """
    Validate an IncidentReport deterministically against verified ground-truth telemetry
    and explicit severity cues in the raw incident text.
    """
    violations: list[str] = []
    suggestions: list[str] = []
    min_risk = 1
    min_congestion = "low"

    def raise_min_risk(value: int) -> None:
        nonlocal min_risk
        if value > min_risk:
            min_risk = value

    def raise_min_congestion(level: str) -> None:
        nonlocal min_congestion
        if CONGESTION_RANK[level] > CONGESTION_RANK[min_congestion]:
            min_congestion = level

    flow = telemetry.get("traffic_flow") or {}
    weather = telemetry.get("weather") or {}
    nearby = telemetry.get("nearby_incidents") or {}

    if report.congestion_level not in CONGESTION_RANK:
        violations.append(f"Invalid congestion level for non-escalated report: {report.congestion_level}")
        suggestions.append("Set congestion_level to one of: low, medium, high, critical.")

    if report.risk_score is None or not (1 <= report.risk_score <= 10):
        violations.append(f"Risk score {report.risk_score} is missing or out of valid range [1-10].")
        suggestions.append("Provide an integer risk_score between 1 and 10.")

    current_risk = report.risk_score if report.risk_score is not None else 0
    current_rank = CONGESTION_RANK.get(report.congestion_level, 0)

    # Rule 1: TomTom live road closure
    if flow.get("available") and flow.get("road_closure") is True:
        raise_min_congestion("critical")
        raise_min_risk(8)
        if report.congestion_level != "critical" or current_risk < 8:
            violations.append(
                "TomTom Flow reports road_closure=true, requiring congestion_level='critical' and risk_score >= 8."
            )
            suggestions.append("Upgrade congestion_level to 'critical' and set risk_score >= 8.")

    # Rule 2: TomTom live speed ratio
    speed_ratio = flow.get("speed_ratio") if flow.get("available") else None
    if speed_ratio is not None:
        if speed_ratio <= 0.25:
            raise_min_congestion("high")
            raise_min_risk(7)
            if current_rank < CONGESTION_RANK["high"] or current_risk < 7:
                violations.append(
                    f"TomTom live speed_ratio={speed_ratio} (severe slowdown <= 0.25) requires congestion_level >= high and risk_score >= 7."
                )
                suggestions.append("Set congestion_level to 'high' or 'critical' and risk_score >= 7.")
        elif speed_ratio <= 0.50:
            raise_min_congestion("medium")
            raise_min_risk(5)
            if current_rank < CONGESTION_RANK["medium"] or current_risk < 5:
                violations.append(
                    f"TomTom live speed_ratio={speed_ratio} (<= 0.50 of free-flow) cannot be LOW congestion or risk_score < 5."
                )
                suggestions.append("Set congestion_level to at least 'medium' and risk_score >= 5.")

    # Rule 3: Open-Meteo verified hazardous weather
    if weather.get("available") and weather.get("hazardous") is True:
        raise_min_risk(6)
        if current_risk < 6:
            violations.append(
                f"Open-Meteo reports hazardous weather ({weather.get('condition')}), requiring risk_score >= 6."
            )
            suggestions.append("Increase risk_score to at least 6 for hazardous weather conditions.")

    # Rule 4: Explicit lane closure cues in incident text
    if _ALL_LANES_RE.search(incident_text or ""):
        raise_min_congestion("high")
        raise_min_risk(7)
        if current_rank < CONGESTION_RANK["high"] or current_risk < 7:
            violations.append("Incident states all lanes blocked, requiring congestion_level >= high and risk_score >= 7.")
            suggestions.append("Set congestion_level to 'high' or 'critical' and risk_score >= 7.")
    else:
        lanes_blocked = extract_lanes_blocked(incident_text)
        if lanes_blocked is not None and lanes_blocked >= 3:
            raise_min_congestion("high")
            raise_min_risk(6)
            if current_rank < CONGESTION_RANK["high"] or current_risk < 6:
                violations.append(
                    f"{lanes_blocked} lanes blocked requires congestion_level >= high and risk_score >= 6."
                )
                suggestions.append("Upgrade congestion_level to at least 'high' and risk_score >= 6.")

    # Rule 5: Peak/rush hour + active incident
    if is_peak_hour(incident_text):
        raise_min_congestion("medium")
        if current_rank < CONGESTION_RANK["medium"]:
            violations.append("Peak/rush-hour incident cannot have LOW congestion.")
            suggestions.append("Upgrade congestion_level to at least 'medium'.")

    # Rule 6: Compounding major/road-closed TomTom nearby incidents
    if nearby.get("available"):
        incidents = nearby.get("incidents") or []
        has_major_nearby = any(
            (inc.get("delay_magnitude") in {"major", "undefined"} or inc.get("category") == "road_closed")
            for inc in incidents
        )
        if has_major_nearby:
            raise_min_risk(6)
            if current_risk < 6:
                violations.append(
                    "TomTom Incident Details reports compounding major or road-closed incident nearby, requiring risk_score >= 6."
                )
                suggestions.append("Increase risk_score to at least 6 due to compounding nearby incident.")

    return {
        "valid": len(violations) == 0,
        "violations": violations,
        "suggestions": suggestions,
        "min_risk_score": min_risk,
        "min_congestion_level": min_congestion,
    }


def apply_safe_overrides(report: IncidentReport, validation: dict) -> IncidentReport:
    """
    Deterministically enforce minimum risk_score and congestion_level bounds when the LLM
    fails to resolve rule violations within the allowed retry budget.
    """
    min_risk = validation.get("min_risk_score", 1)
    min_congestion = validation.get("min_congestion_level", "low")

    raw_risk = report.risk_score if report.risk_score is not None else min_risk
    clamped_risk = min(10, max(1, raw_risk, min_risk))

    current_rank = CONGESTION_RANK.get(report.congestion_level, 0)
    min_rank = CONGESTION_RANK.get(min_congestion, 1)
    updated_congestion = (
        min_congestion
        if current_rank < min_rank
        else report.congestion_level
    )

    return report.model_copy(
        update={
            "risk_score": clamped_risk,
            "congestion_level": updated_congestion,
        }
    )
