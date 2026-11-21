"""
External telemetry and dispatch tools for Traffo.
Integrates OpenStreetMap Nominatim + Open-Meteo (geocoding & live weather)
and TomTom Traffic APIs (Flow Segment Data v4 & Incident Details v5).
"""

import math
import os
from datetime import datetime
import requests

NOMINATIM_HEADERS = {
    "User-Agent": "Traffo/2.0 (traffic-triage-copilot)",
    "Accept-Language": "en",
}

FRC_LABELS = {
    "FRC0": "Motorway / Expressway",
    "FRC1": "Major Road",
    "FRC2": "Other Major Road",
    "FRC3": "Secondary Road",
    "FRC4": "Local Connecting Road",
    "FRC5": "Local High-Importance Road",
    "FRC6": "Local Road",
}

TOMTOM_ICON_CATEGORIES = {
    0: "unknown",
    1: "accident",
    2: "fog",
    3: "dangerous_conditions",
    4: "rain",
    5: "ice",
    6: "jam",
    7: "lane_closed",
    8: "road_closed",
    9: "road_works",
    10: "wind",
    11: "flooding",
    14: "broken_down_vehicle",
}

TOMTOM_DELAY_MAGNITUDE = {
    0: "unknown",
    1: "minor",
    2: "moderate",
    3: "major",
    4: "undefined",
}

_DELAY_MAGNITUDE_RANK = {
    "unknown": 0,
    "minor": 1,
    "moderate": 2,
    "major": 3,
    "undefined": 4,
}


def geocode_location(query: str) -> dict:
    """Resolve a street, junction, landmark, or city name to lat/lon coordinates."""
    cleaned = (query or "").strip()
    if not cleaned:
        return {"found": False, "error": "Location query cannot be empty."}

    # 1. Try OpenStreetMap Nominatim first (supports streets, junctions, landmarks)
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"format": "json", "q": cleaned, "limit": 1},
            headers=NOMINATIM_HEADERS,
            timeout=6,
        )
        r.raise_for_status()
        results = r.json()
        if results:
            hit = results[0]
            return {
                "found": True,
                "query": cleaned,
                "label": hit.get("display_name", cleaned),
                "lat": float(hit["lat"]),
                "lon": float(hit["lon"]),
                "source": "nominatim",
            }
    except Exception:
        pass

    # 2. Fallback to Open-Meteo Geocoding API (cities/towns)
    try:
        r = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": cleaned, "count": 1},
            timeout=5,
        )
        r.raise_for_status()
        geo = r.json()
        results = geo.get("results") or []
        if results:
            hit = results[0]
            parts = [hit.get("name"), hit.get("admin1"), hit.get("country")]
            label = ", ".join(p for p in parts if p) or cleaned
            return {
                "found": True,
                "query": cleaned,
                "label": label,
                "lat": float(hit["latitude"]),
                "lon": float(hit["longitude"]),
                "source": "open-meteo",
            }
    except Exception as e:
        return {"found": False, "query": cleaned, "error": f"Geocoding error: {e}"}

    return {"found": False, "query": cleaned, "error": f"Could not geocode location: {cleaned}"}


def get_weather(lat: float, lon: float, label: str = "") -> dict:
    """Fetch live weather at exact coordinates using Open-Meteo (free, no key)."""
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,precipitation,weathercode,windspeed_10m,visibility",
                "timezone": "auto",
            },
            timeout=6,
        )
        r.raise_for_status()
        w = r.json().get("current", {})
        code = w.get("weathercode", 0)

        weather_desc = {
            range(0, 1): "Clear sky",
            range(1, 4): "Partly cloudy",
            range(45, 48): "Foggy",
            range(51, 68): "Drizzle/Rain",
            range(71, 78): "Snow",
            range(80, 83): "Rain showers",
            range(95, 100): "Thunderstorm",
        }
        description = next((v for k, v in weather_desc.items() if code in k), "Unknown")
        visibility_m = w.get("visibility")
        visibility_km = round(visibility_m / 1000, 1) if visibility_m is not None else "N/A"

        return {
            "available": True,
            "source": "open-meteo",
            "location": label or f"{float(lat):.4f},{float(lon):.4f}",
            "coordinates": {"lat": float(lat), "lon": float(lon)},
            "temperature_c": w.get("temperature_2m"),
            "precipitation_mm": w.get("precipitation"),
            "wind_speed_kmh": w.get("windspeed_10m"),
            "visibility_km": visibility_km,
            "condition": description,
            "hazardous": description in {"Foggy", "Thunderstorm", "Rain showers", "Drizzle/Rain", "Snow"},
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


def get_traffic_flow(lat: float, lon: float, label: str = "") -> dict:
    """Fetch live road segment speed and closure telemetry from TomTom Flow v4."""
    api_key = os.environ.get("TOMTOM_API_KEY", "").strip()
    if not api_key:
        return {
            "available": False,
            "error": "TOMTOM_API_KEY not configured. Live traffic speed unavailable.",
        }

    try:
        r = requests.get(
            "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json",
            params={
                "key": api_key,
                "point": f"{lat},{lon}",
                "unit": "Kmh",
            },
            timeout=8,
        )
        r.raise_for_status()
        data = r.json().get("flowSegmentData", {})
        if not data:
            return {
                "available": False,
                "error": "TomTom returned no flowSegmentData for these coordinates.",
            }

        current_speed = data.get("currentSpeed")
        free_flow_speed = data.get("freeFlowSpeed")
        speed_ratio = (
            round(current_speed / free_flow_speed, 2)
            if current_speed is not None and free_flow_speed and free_flow_speed > 0
            else None
        )
        curr_tt = data.get("currentTravelTime") or 0
        free_tt = data.get("freeFlowTravelTime") or 0
        delay_seconds = max(0, curr_tt - free_tt)
        road_closure = bool(data.get("roadClosure", False))
        frc = data.get("frc", "")

        if road_closure or (speed_ratio is not None and speed_ratio <= 0.25):
            flow_congestion = "critical"
        elif speed_ratio is not None and speed_ratio <= 0.50:
            flow_congestion = "high"
        elif speed_ratio is not None and speed_ratio <= 0.75:
            flow_congestion = "medium"
        elif speed_ratio is not None:
            flow_congestion = "low"
        else:
            flow_congestion = "unknown"

        return {
            "available": True,
            "source": "tomtom_flow_v4",
            "location": label or f"{float(lat):.4f},{float(lon):.4f}",
            "coordinates": {"lat": float(lat), "lon": float(lon)},
            "frc": frc,
            "road_class": FRC_LABELS.get(frc, frc or "Unknown"),
            "current_speed_kmh": current_speed,
            "free_flow_speed_kmh": free_flow_speed,
            "speed_ratio": speed_ratio,
            "delay_seconds": delay_seconds,
            "road_closure": road_closure,
            "data_confidence": data.get("confidence"),
            "flow_congestion_level": flow_congestion,
        }
    except Exception as e:
        return {"available": False, "error": f"TomTom flow API error: {e}"}


def _extract_representative_lat_lon(geometry: dict) -> tuple[float | None, float | None]:
    """Convert GeoJSON [lon, lat] geometry into a representative (lat, lon) pair."""
    if not isinstance(geometry, dict):
        return None, None
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return None, None

    if gtype == "Point" and len(coords) >= 2:
        return float(coords[1]), float(coords[0])
    if gtype == "LineString" and len(coords) >= 1:
        mid = coords[len(coords) // 2]
        if len(mid) >= 2:
            return float(mid[1]), float(mid[0])
    if gtype == "Polygon" and len(coords) >= 1 and len(coords[0]) >= 1:
        first = coords[0][0]
        if len(first) >= 2:
            return float(first[1]), float(first[0])
    return None, None


def _decode_category(raw_val) -> str:
    if isinstance(raw_val, int):
        return TOMTOM_ICON_CATEGORIES.get(raw_val, "unknown")
    if isinstance(raw_val, str):
        if raw_val.isdigit():
            return TOMTOM_ICON_CATEGORIES.get(int(raw_val), "unknown")
        return raw_val
    return "unknown"


def _decode_delay_magnitude(raw_val) -> tuple[str, int]:
    if isinstance(raw_val, int):
        label = TOMTOM_DELAY_MAGNITUDE.get(raw_val, "unknown")
        return label, raw_val
    if isinstance(raw_val, str):
        if raw_val.isdigit():
            code = int(raw_val)
            return TOMTOM_DELAY_MAGNITUDE.get(code, "unknown"), code
        norm = raw_val.strip().lower()
        return norm, _DELAY_MAGNITUDE_RANK.get(norm, 0)
    return "unknown", 0


def get_nearby_incidents(lat: float, lon: float, radius_km: float = 3.0) -> dict:
    """Fetch active nearby traffic incidents around coordinates from TomTom Incident Details v5."""
    api_key = os.environ.get("TOMTOM_API_KEY", "").strip()
    if not api_key:
        return {
            "available": False,
            "error": "TOMTOM_API_KEY not configured. Live incident feed unavailable.",
            "incidents": [],
        }

    try:
        clamped_radius = max(0.5, min(float(radius_km), 10.0))
        dlat = clamped_radius / 111.32
        dlon = clamped_radius / (111.32 * max(math.cos(math.radians(float(lat))), 0.01))
        bbox = f"{lon - dlon:.5f},{lat - dlat:.5f},{lon + dlon:.5f},{lat + dlat:.5f}"

        fields = (
            "{incidents{type,geometry{type,coordinates},"
            "properties{id,iconCategory,magnitudeOfDelay,"
            "events{description,code},startTime,endTime,from,to,length,delay,roadNumbers}}}"
        )

        r = requests.get(
            "https://api.tomtom.com/traffic/services/5/incidentDetails",
            params={
                "key": api_key,
                "bbox": bbox,
                "fields": fields,
                "language": "en-GB",
                "timeValidityFilter": "present",
            },
            timeout=8,
        )
        r.raise_for_status()
        raw_incidents = r.json().get("incidents") or []

        parsed_items = []
        for feat in raw_incidents:
            props = feat.get("properties") or {}
            geom = feat.get("geometry") or {}
            rep_lat, rep_lon = _extract_representative_lat_lon(geom)
            category = _decode_category(props.get("iconCategory"))
            delay_label, delay_rank = _decode_delay_magnitude(props.get("magnitudeOfDelay"))
            events = props.get("events") or []
            descriptions = [
                ev.get("description")
                for ev in events
                if isinstance(ev, dict) and ev.get("description")
            ]
            delay_sec = props.get("delay") or 0

            parsed_items.append({
                "id": props.get("id"),
                "category": category,
                "delay_magnitude": delay_label,
                "_delay_rank": delay_rank,
                "delay_seconds": delay_sec,
                "length_m": props.get("length"),
                "road_numbers": props.get("roadNumbers") or [],
                "from_road": props.get("from"),
                "to_road": props.get("to"),
                "start_time": props.get("startTime"),
                "descriptions": descriptions,
                "lat": rep_lat,
                "lon": rep_lon,
            })

        parsed_items.sort(
            key=lambda x: (x["_delay_rank"], x["delay_seconds"] or 0),
            reverse=True,
        )

        top_incidents = []
        for item in parsed_items[:8]:
            item.pop("_delay_rank", None)
            top_incidents.append(item)

        return {
            "available": True,
            "source": "tomtom_incidents_v5",
            "radius_km": clamped_radius,
            "total_found": len(raw_incidents),
            "incidents": top_incidents,
        }
    except Exception as e:
        return {
            "available": False,
            "error": f"TomTom incident details API error: {e}",
            "incidents": [],
        }


def escalate_to_human(reason: str, situation_summary: str) -> dict:
    """Structured handoff to a human traffic operator when agent cannot confidently decide."""
    now = datetime.now()
    return {
        "escalated": True,
        "timestamp": now.isoformat(),
        "reason": reason,
        "situation_summary": situation_summary,
        "priority": (
            "HIGH"
            if any(w in reason.lower() for w in ["critical", "unknown", "unclear", "conflicting"])
            else "MEDIUM"
        ),
        "recommended_operator_action": "Review incident details, verify CCTV/CAD feeds, and override system triage if needed",
        "handoff_id": f"TRF-{now.strftime('%Y%m%d%H%M%S')}",
    }


TOOLS = {
    "geocode_location": geocode_location,
    "get_weather": get_weather,
    "get_traffic_flow": get_traffic_flow,
    "get_nearby_incidents": get_nearby_incidents,
    "escalate_to_human": escalate_to_human,
}
