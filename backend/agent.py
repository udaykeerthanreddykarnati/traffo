"""
Traffo Agent — AI-Assisted Traffic Incident Triage & Dispatch Copilot
Uses Groq (Llama 3.3 70B) in a custom ReAct loop backed by real-time
TomTom Traffic Flow v4, TomTom Incident Details v5, Open-Meteo weather,
Nominatim geocoding, deterministic post-generation validation, and terminal
human-operator escalation.
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from groq import Groq
from pydantic import ValidationError

from schemas import IncidentReport
from tools import TOOLS, get_nearby_incidents, get_traffic_flow, get_weather
from validator import apply_safe_overrides, validate_report

# Load .env from repo root and backend/ without overwriting exported env vars
_BASE_DIR = Path(__file__).resolve().parent
load_dotenv(_BASE_DIR.parent / ".env", override=False)
load_dotenv(_BASE_DIR / ".env", override=False)


def _get_groq_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise ValueError("GROQ_API_KEY is not set. Add it to .env or your environment.")
    return Groq(api_key=api_key)


TOOL_DESCRIPTIONS = """
You have access to these tools. Call one tool per turn by outputting ONLY valid JSON:
{"tool": "name", "args": {...}}

1. geocode_location(query: str)
   -> Resolves a street, junction, landmark, highway segment, or city name to exact lat/lon coordinates.
      Use this first whenever coordinates are not already provided.

2. get_weather(lat: float, lon: float, label: str = "")
   -> Returns live weather (temperature, precipitation, wind speed, visibility, hazardous flag) at coordinates.

3. get_traffic_flow(lat: float, lon: float, label: str = "")
   -> Queries TomTom Traffic Flow v4 for live current speed vs free-flow speed, speed_ratio, delay_seconds, road_class, and road_closure status.

4. get_nearby_incidents(lat: float, lon: float, radius_km: float = 3.0)
   -> Queries TomTom Traffic Incident Details v5 for active nearby accidents, jams, lane/road closures, and roadworks around the coordinates.

5. escalate_to_human(reason: str, situation_summary: str)
   -> Escalates to a human traffic operator if the incident lacks a usable location, lacks clear severity cues, or has unresolvable conflicting signals.
      This ends your analysis immediately — only call this when you genuinely cannot proceed.

When you have gathered telemetry and are ready to finalize, output ONLY valid JSON:
{"final_response": {
    "congestion_level": "low|medium|high|critical",
    "risk_score": 1-10,
    "primary_cause": "...",
    "affected_area": "...",
    "recommended_actions": ["concrete operator/dispatch playbook action 1", "action 2"],
    "public_broadcast": "Concise draft motorist alert pending operator sign-off...",
    "reasoning_summary": "...",
    "confidence": "low|medium|high"
}}
"""

BASE_SYSTEM_PROMPT = """You are Traffo, an AI-assisted Traffic Incident Triage & Dispatch Copilot.

Your job: take a natural-language dispatch or incident report, gather live telemetry using your tools, and produce a structured triage ticket and operator action plan for human review.

CRITICAL RULES:
1. Copy place names accurately when calling geocode_location.
2. Do NOT fabricate live traffic speeds, delays, weather, or nearby incidents. If TomTom returns available=false (e.g., key not configured), explicitly acknowledge that live traffic telemetry was unavailable and base your assessment transparently on the report + available tools.
3. Recommend concrete operator/dispatch playbook actions (e.g., verify via CCTV, dispatch patrol/tow/EMS, coordinate corridor clearance, issue diversion alert). Never invent specific traffic signal phase timings.
4. Keep public_broadcast concise and suitable as a draft motorist advisory pending operator approval.
5. Every final_response is automatically checked by a deterministic Python validator against the fetched telemetry and incident severity cues before delivery.
6. If the incident has no usable location, no clear severity, or unresolvable conflicting signals, call escalate_to_human instead of guessing.

Reasoning workflow:
1. Parse the incident (location, time, weather cues, lanes blocked, severity).
2. If pre-fetched telemetry for a pinned map location is NOT already provided below, call geocode_location first, then check get_weather, get_traffic_flow, and get_nearby_incidents.
3. Synthesize live telemetry (speed_ratio, road_closure, compounding TomTom incidents, hazardous weather) with the incident text.
4. Output your final_response JSON.

Output ONLY valid JSON for tool calls and final_response. No markdown prose outside JSON.
""" + TOOL_DESCRIPTIONS


MAP_LOCATION_PATTERN = re.compile(
    r"MAP LOCATION:\s*lat=(-?\d+\.?\d*),\s*lon=(-?\d+\.?\d*),\s*label=(.+)"
)


def extract_map_location(incident_description: str) -> Optional[tuple[float, float, str]]:
    """Return (lat, lon, label) if a MAP LOCATION line is present in text, else None."""
    match = MAP_LOCATION_PATTERN.search(incident_description or "")
    if not match:
        return None
    lat, lon, label = match.groups()
    return float(lat), float(lon), label.strip()


def extract_json_object(raw: str) -> Optional[dict]:
    """
    Robustly extract the first valid JSON object from raw LLM output,
    handling markdown code fences and surrounding prose.
    """
    if not raw or not isinstance(raw, str):
        return None

    cleaned = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned, re.IGNORECASE)
    candidates = [fence_match.group(1).strip()] if fence_match else []
    candidates.append(cleaned)

    decoder = json.JSONDecoder()
    for text in candidates:
        for idx, ch in enumerate(text):
            if ch != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(text[idx:])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
    return None


def build_telemetry_summary(telemetry: dict) -> dict:
    """Build a compact summary of fetched telemetry for the final report and UI."""
    flow = telemetry.get("traffic_flow") or {}
    weather = telemetry.get("weather") or {}
    nearby = telemetry.get("nearby_incidents") or {}
    loc = telemetry.get("location") or {}

    return {
        "location": {
            "lat": loc.get("lat"),
            "lon": loc.get("lon"),
            "label": loc.get("label"),
        } if loc else None,
        "weather": {
            "available": bool(weather.get("available")),
            "condition": weather.get("condition"),
            "temperature_c": weather.get("temperature_c"),
            "precipitation_mm": weather.get("precipitation_mm"),
            "wind_speed_kmh": weather.get("wind_speed_kmh"),
            "visibility_km": weather.get("visibility_km"),
            "hazardous": weather.get("hazardous"),
            "error": weather.get("error"),
        } if weather else None,
        "traffic_flow": {
            "available": bool(flow.get("available")),
            "road_class": flow.get("road_class"),
            "current_speed_kmh": flow.get("current_speed_kmh"),
            "free_flow_speed_kmh": flow.get("free_flow_speed_kmh"),
            "speed_ratio": flow.get("speed_ratio"),
            "delay_seconds": flow.get("delay_seconds"),
            "road_closure": flow.get("road_closure"),
            "flow_congestion_level": flow.get("flow_congestion_level"),
            "error": flow.get("error"),
        } if flow else None,
        "nearby_incidents": {
            "available": bool(nearby.get("available")),
            "total_found": nearby.get("total_found", 0),
            "returned": len(nearby.get("incidents") or []),
            "error": nearby.get("error"),
        } if nearby else None,
    }


def _coords_match(lat1: float, lon1: float, lat2, lon2, tol: float = 0.005) -> bool:
    try:
        return abs(float(lat1) - float(lat2)) <= tol and abs(float(lon1) - float(lon2)) <= tol
    except (TypeError, ValueError):
        return False


def run_agent(incident_description: str, pinned_location: Optional[dict] = None):
    """
    Main agent loop. Yields streaming updates as (type, content) tuples.
    Types: "thought", "tool_call", "tool_result", "validation", "final", "error"
    """
    try:
        groq_client = _get_groq_client()
    except Exception as e:
        yield ("error", str(e))
        return

    clean_incident = MAP_LOCATION_PATTERN.sub("", incident_description or "").strip()
    if not clean_incident:
        clean_incident = (incident_description or "").strip()

    user_message = (
        f"INCIDENT: {clean_incident}\n\n"
        "Copy place names accurately when calling geocode_location."
    )

    telemetry: dict = {}
    pinned_coords: Optional[tuple[float, float, str]] = None

    if pinned_location and "lat" in pinned_location and "lon" in pinned_location:
        pinned_coords = (
            float(pinned_location["lat"]),
            float(pinned_location["lon"]),
            (pinned_location.get("label") or "").strip(),
        )
    else:
        parsed_map = extract_map_location(incident_description)
        if parsed_map:
            pinned_coords = parsed_map

    # Deterministic pre-loop telemetry fetch when coordinates are pinned on the map
    if pinned_coords:
        lat, lon, label = pinned_coords
        display_label = label or f"{lat:.4f},{lon:.4f}"
        telemetry["location"] = {"lat": lat, "lon": lon, "label": display_label}

        yield ("tool_call", {"tool": "get_weather", "args": {"lat": lat, "lon": lon, "label": display_label}})
        weather_data = get_weather(lat, lon, display_label)
        telemetry["weather"] = weather_data
        yield ("tool_result", {"tool": "get_weather", "result": weather_data})

        yield ("tool_call", {"tool": "get_traffic_flow", "args": {"lat": lat, "lon": lon, "label": display_label}})
        flow_data = get_traffic_flow(lat, lon, display_label)
        telemetry["traffic_flow"] = flow_data
        yield ("tool_result", {"tool": "get_traffic_flow", "result": flow_data})

        yield ("tool_call", {"tool": "get_nearby_incidents", "args": {"lat": lat, "lon": lon, "radius_km": 3.0}})
        nearby_data = get_nearby_incidents(lat, lon, 3.0)
        telemetry["nearby_incidents"] = nearby_data
        yield ("tool_result", {"tool": "get_nearby_incidents", "result": nearby_data})

        user_message += (
            f"\n\nPRE-FETCHED TELEMETRY FOR PINNED LOCATION '{display_label}' ({lat}, {lon}) — "
            "do not call geocode_location, get_weather, get_traffic_flow, or get_nearby_incidents again for this point:"
            f"\n- WEATHER: {json.dumps(weather_data)}"
            f"\n- TRAFFIC FLOW: {json.dumps(flow_data)}"
            f"\n- NEARBY INCIDENTS: {json.dumps(nearby_data)}"
        )

    messages = [
        {"role": "system", "content": BASE_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    max_iterations = 8
    max_validation_retries = 2
    iteration = 0
    validation_retries = 0
    caught_violations: list[str] = []

    while iteration < max_iterations:
        iteration += 1

        try:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=0.2,
                max_tokens=1500,
            )
            raw = (response.choices[0].message.content or "").strip()
        except Exception as e:
            yield ("error", f"Groq API error: {e}")
            return

        parsed = extract_json_object(raw)
        if not parsed:
            yield ("thought", raw)
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": "Continue your analysis. Respond with ONLY valid JSON for a tool call or final_response.",
            })
            continue

        # ── Final response with mandatory deterministic validation ────────────
        if "final_response" in parsed:
            candidate = parsed["final_response"]
            if not isinstance(candidate, dict):
                validation_retries += 1
                yield ("validation", {"valid": False, "stage": "schema", "violations": ["final_response must be a JSON object."]})
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": "final_response must be a JSON object matching the schema. Fix and retry."})
                continue

            try:
                report = IncidentReport(**candidate)
            except ValidationError as ve:
                err_msgs = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in ve.errors()]
                yield ("validation", {"valid": False, "stage": "schema", "violations": err_msgs})
                if validation_retries < max_validation_retries:
                    validation_retries += 1
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({
                        "role": "user",
                        "content": (
                            f"Your final_response failed schema validation: {'; '.join(err_msgs)}. "
                            "Fix the fields and output valid final_response JSON."
                        ),
                    })
                    continue
                yield ("error", f"Final response failed schema validation: {'; '.join(err_msgs)}")
                return

            validation = validate_report(report, telemetry, clean_incident)
            yield ("validation", validation)

            if not validation["valid"]:
                caught_violations.extend(validation["violations"])
                if validation_retries < max_validation_retries:
                    validation_retries += 1
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({
                        "role": "user",
                        "content": (
                            "Deterministic guardrail check FAILED:\n"
                            f"- Violations: {'; '.join(validation['violations'])}\n"
                            f"- Required fixes: {'; '.join(validation['suggestions'])}\n"
                            "Update your final_response JSON to satisfy all constraints."
                        ),
                    })
                    continue

                report = apply_safe_overrides(report, validation)
                final_payload = report.model_copy(
                    update={
                        "telemetry_summary": build_telemetry_summary(telemetry),
                        "validation": {
                            "passed": False,
                            "violations_caught": list(dict.fromkeys(caught_violations)),
                            "auto_adjusted": True,
                        },
                    }
                ).model_dump()
                yield ("final", final_payload)
                return

            final_payload = report.model_copy(
                update={
                    "telemetry_summary": build_telemetry_summary(telemetry),
                    "validation": {
                        "passed": True,
                        "violations_caught": list(dict.fromkeys(caught_violations)),
                        "auto_adjusted": False,
                    },
                }
            ).model_dump()
            yield ("final", final_payload)
            return

        # ── Tool call ─────────────────────────────────────────────────────────
        if "tool" in parsed:
            tool_name = parsed.get("tool")
            tool_args = parsed.get("args") if isinstance(parsed.get("args"), dict) else {}

            # Short-circuit redundant calls for already pre-fetched pinned location
            if pinned_coords and tool_name in ("geocode_location", "get_weather", "get_traffic_flow", "get_nearby_incidents"):
                p_lat, p_lon, _ = pinned_coords
                same_point = (
                    tool_name == "geocode_location"
                    or _coords_match(p_lat, p_lon, tool_args.get("lat"), tool_args.get("lon"))
                    or ("lat" not in tool_args and "lon" not in tool_args)
                )
                if same_point:
                    cached_key = {
                        "geocode_location": "location",
                        "get_weather": "weather",
                        "get_traffic_flow": "traffic_flow",
                        "get_nearby_incidents": "nearby_incidents",
                    }[tool_name]
                    note = {
                        "note": f"Already pre-fetched for pinned location. Use the {cached_key} data already provided.",
                        "data": telemetry.get(cached_key),
                    }
                    yield ("tool_call", {"tool": tool_name, "args": tool_args})
                    yield ("tool_result", {"tool": tool_name, "result": note})
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({
                        "role": "user",
                        "content": f"Tool result for {tool_name}: {json.dumps(note)}\n\nContinue your analysis using the pre-fetched telemetry.",
                    })
                    continue

            yield ("tool_call", {"tool": tool_name, "args": tool_args})

            if tool_name not in TOOLS:
                tool_result = {"error": f"Unknown tool: {tool_name}"}
            else:
                try:
                    tool_result = TOOLS[tool_name](**tool_args)
                except Exception as e:
                    tool_result = {"error": str(e)}

            # Record verified telemetry for deterministic post-generation validation
            if isinstance(tool_result, dict):
                if tool_name == "geocode_location" and tool_result.get("found"):
                    telemetry["location"] = {
                        "lat": tool_result.get("lat"),
                        "lon": tool_result.get("lon"),
                        "label": tool_result.get("label"),
                    }
                elif tool_name == "get_weather":
                    telemetry["weather"] = tool_result
                elif tool_name == "get_traffic_flow":
                    telemetry["traffic_flow"] = tool_result
                elif tool_name == "get_nearby_incidents":
                    telemetry["nearby_incidents"] = tool_result

            yield ("tool_result", {"tool": tool_name, "result": tool_result})

            # Escalation is a hard stop in control flow
            if tool_name == "escalate_to_human":
                escalation_report = IncidentReport(
                    congestion_level="unknown",
                    risk_score=None,
                    primary_cause="Escalated to human operator",
                    affected_area=tool_args.get("situation_summary", "Unknown"),
                    recommended_actions=[
                        "Await human operator review",
                        "Verify incident location and severity via CCTV or dispatch radio",
                    ],
                    public_broadcast="Incident under operator review; verified advisory pending.",
                    reasoning_summary=tool_args.get(
                        "reason",
                        "Escalated to human operator due to insufficient or conflicting information.",
                    ),
                    confidence="low",
                    escalated=True,
                    escalation_details=tool_result,
                    telemetry_summary=build_telemetry_summary(telemetry),
                    validation={"passed": True, "violations_caught": [], "auto_adjusted": False},
                )
                yield ("final", escalation_report.model_dump())
                return

            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": f"Tool result for {tool_name}: {json.dumps(tool_result)}\n\nContinue your analysis.",
            })
        else:
            yield ("thought", raw)
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": "Continue. Call a tool or provide final_response JSON.",
            })

    yield ("error", "Agent reached maximum iterations without a final response.")
