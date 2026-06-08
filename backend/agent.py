"""
Traffo Agent — Agentic Traffic Incident Response System
Uses Groq (free) as the LLM backbone with Llama 3.3 70B.
Tools: weather, routing, news search, rule validator, escalation
"""

import os
import json
import re
import requests
from datetime import datetime
from groq import Groq

# ── Configure Groq ────────────────────────────────────────────────────────────
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))

# ── Tool Implementations ──────────────────────────────────────────────────────

def get_weather(location: str) -> dict:
    """Fetch live weather for a location using Open-Meteo (completely free, no key)."""
    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={requests.utils.quote(location)}&count=1"
        geo = requests.get(geo_url, timeout=5).json()
        if not geo.get("results"):
            return {"error": f"Could not find location: {location}"}
        result = geo["results"][0]
        lat, lon = result["latitude"], result["longitude"]
        city = result.get("name", location)

        weather_url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,precipitation,weathercode,windspeed_10m,visibility"
            f"&timezone=auto"
        )
        w = requests.get(weather_url, timeout=5).json().get("current", {})
        code = w.get("weathercode", 0)

        weather_desc = {
            range(0, 1): "Clear sky", range(1, 4): "Partly cloudy",
            range(45, 48): "Foggy", range(51, 68): "Drizzle/Rain",
            range(71, 78): "Snow", range(80, 83): "Rain showers",
            range(95, 100): "Thunderstorm"
        }
        description = next((v for k, v in weather_desc.items() if code in k), "Unknown")
        visibility_m = w.get("visibility", 10000)
        visibility_km = round(visibility_m / 1000, 1) if visibility_m else "N/A"

        return {
            "location": city,
            "temperature_c": w.get("temperature_2m"),
            "precipitation_mm": w.get("precipitation"),
            "wind_speed_kmh": w.get("windspeed_10m"),
            "visibility_km": visibility_km,
            "condition": description,
            "hazardous": description in ["Foggy", "Thunderstorm", "Rain showers", "Drizzle/Rain"]
        }
    except Exception as e:
        return {"error": str(e)}


def get_alternate_routes(origin: str, destination: str) -> dict:
    """Get alternate route suggestions. Uses simulated routes if no ORS key."""
    api_key = os.environ.get("ORS_API_KEY", "")
    if not api_key:
        return {
            "note": "Simulated routes (add ORS_API_KEY for live data)",
            "routes": [
                {"name": "Primary via main arterial", "estimated_extra_minutes": 0, "congestion_risk": "high"},
                {"name": "Alternate via inner ring road", "estimated_extra_minutes": 12, "congestion_risk": "medium"},
                {"name": "Bypass via outer highway", "estimated_extra_minutes": 22, "congestion_risk": "low"},
            ]
        }
    try:
        headers = {"Authorization": api_key, "Content-Type": "application/json"}
        def geocode(place):
            r = requests.get(
                f"https://api.openrouteservice.org/geocode/search?api_key={api_key}&text={requests.utils.quote(place)}&size=1",
                timeout=5
            ).json()
            return r["features"][0]["geometry"]["coordinates"]

        orig_coords = geocode(origin)
        dest_coords = geocode(destination)
        body = {
            "coordinates": [orig_coords, dest_coords],
            "alternative_routes": {"target_count": 3, "weight_factor": 1.6}
        }
        r = requests.post(
            "https://api.openrouteservice.org/v2/directions/driving-car/json",
            json=body, headers=headers, timeout=10
        ).json()
        routes = []
        for i, route in enumerate(r.get("routes", [])):
            seg = route["summary"]
            routes.append({
                "name": f"Route {i+1}",
                "distance_km": round(seg["distance"] / 1000, 1),
                "duration_min": round(seg["duration"] / 60, 1),
            })
        return {"origin": origin, "destination": destination, "routes": routes}
    except Exception as e:
        return {"error": str(e)}


def search_traffic_news(location: str) -> dict:
    """Search for recent traffic incidents using DuckDuckGo (no API key needed)."""
    try:
        query = f"traffic incident accident {location} today"
        url = f"https://duckduckgo.com/html/?q={requests.utils.quote(query)}"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=8)
        snippets = re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', r.text)
        clean = [re.sub(r'<[^>]+>', '', s).strip() for s in snippets[:4]]
        return {
            "query": query,
            "results": clean if clean else ["No recent incidents found in news sources."]
        }
    except Exception as e:
        return {"error": str(e), "results": ["Could not fetch news."]}


def validate_decision(
    congestion_level: str,
    risk_score: int,
    weather_condition: str,
    lanes_blocked: int,
    time_of_day: str
) -> dict:
    """Rule-based constraint validator — catches LLM hallucinations and logical inconsistencies."""
    violations = []
    suggestions = []

    if weather_condition.lower() in ["foggy", "thunderstorm", "rain showers", "drizzle/rain"]:
        if risk_score < 6:
            violations.append(f"Risk score {risk_score} is too low for hazardous weather ({weather_condition})")
            suggestions.append("Increase risk score to at least 6 for hazardous conditions")

    if lanes_blocked >= 3 and congestion_level.lower() == "low":
        violations.append(f"{lanes_blocked} lanes blocked cannot yield LOW congestion")
        suggestions.append("Upgrade congestion level to HIGH")

    peak_hours = ["7am", "8am", "9am", "5pm", "6pm", "7pm", "8pm", "peak", "rush"]
    if any(p in time_of_day.lower() for p in peak_hours):
        if congestion_level.lower() == "low":
            violations.append("Peak hour incident cannot have LOW congestion")
            suggestions.append("Upgrade to at least MEDIUM congestion")

    if not (1 <= risk_score <= 10):
        violations.append(f"Risk score {risk_score} out of valid range [1-10]")

    valid_congestion = ["low", "medium", "high", "critical"]
    if congestion_level.lower() not in valid_congestion:
        violations.append(f"Invalid congestion level: {congestion_level}")

    return {
        "valid": len(violations) == 0,
        "violations": violations,
        "suggestions": suggestions,
        "validated_risk": max(risk_score, 6) if violations else risk_score,
        "validated_congestion": congestion_level
    }


def escalate_to_human(reason: str, situation_summary: str) -> dict:
    """Structured handoff to human operator when agent cannot confidently decide."""
    return {
        "escalated": True,
        "timestamp": datetime.now().isoformat(),
        "reason": reason,
        "situation_summary": situation_summary,
        "priority": "HIGH" if any(w in reason.lower() for w in ["critical", "unknown", "unclear", "conflicting"]) else "MEDIUM",
        "recommended_operator_action": "Review incident details and override system recommendation if needed",
        "handoff_id": f"TRF-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    }


# ── Tool Registry ─────────────────────────────────────────────────────────────

TOOLS = {
    "get_weather": get_weather,
    "get_alternate_routes": get_alternate_routes,
    "search_traffic_news": search_traffic_news,
    "validate_decision": validate_decision,
    "escalate_to_human": escalate_to_human,
}

TOOL_DESCRIPTIONS = """
You have access to these tools. Call them by outputting JSON with {"tool": "name", "args": {...}}

1. get_weather(location: str)
   → Returns live weather conditions for a location

2. get_alternate_routes(origin: str, destination: str)
   → Returns alternate route options with congestion estimates

3. search_traffic_news(location: str)
   → Searches for recent traffic incidents and news near a location

4. validate_decision(congestion_level: str, risk_score: int, weather_condition: str, lanes_blocked: int, time_of_day: str)
   → Validates your decision against traffic management rules. ALWAYS call this before final output.

5. escalate_to_human(reason: str, situation_summary: str)
   → Escalates to human operator if situation is too ambiguous or critical

When you have enough information and have validated your decision, output your FINAL RESPONSE as:
{"final_response": {
    "congestion_level": "low|medium|high|critical",
    "risk_score": 1-10,
    "primary_cause": "...",
    "affected_area": "...",
    "recommended_actions": ["action1", "action2", ...],
    "signal_adjustments": "...",
    "public_broadcast": "...",
    "reasoning_summary": "...",
    "confidence": "low|medium|high"
}}
"""

SYSTEM_PROMPT = """You are Traffo, an agentic traffic incident response system.

Your job: given a natural language traffic situation, autonomously gather information using your tools and produce a structured incident response plan.

Your reasoning process:
1. Parse the incident: identify location, time, weather mentions, severity cues
2. Always check live weather for the location
3. Search for related news/incidents nearby
4. If a destination is mentioned or implied, check alternate routes
5. Reason about cascading effects (will alternate routes also jam up?)
6. Form your decision (congestion level, risk score, actions)
7. ALWAYS validate your decision using validate_decision before finishing
8. If situation is ambiguous or critical with conflicting signals, escalate

Think step by step. Be methodical. Each tool call should be motivated by a specific gap in your knowledge.
Output ONLY valid JSON for tool calls and final response. No markdown, no explanation outside JSON.

""" + TOOL_DESCRIPTIONS


# ── Agent Loop ────────────────────────────────────────────────────────────────

def run_agent(incident_description: str):
    """
    Main agent loop. Yields streaming updates as (type, content) tuples.
    Types: "thought", "tool_call", "tool_result", "final", "error"
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"INCIDENT: {incident_description}"}
    ]

    max_iterations = 8
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        try:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=0.2,
                max_tokens=1500,
            )
            raw = response.choices[0].message.content.strip()
        except Exception as e:
            yield ("error", f"Groq API error: {str(e)}")
            return

        json_match = re.search(r'\{[\s\S]*\}', raw)

        if not json_match:
            yield ("thought", raw)
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": "Continue your analysis. Use a tool or provide your final response."})
            continue

        try:
            parsed = json.loads(json_match.group())
        except json.JSONDecodeError:
            yield ("thought", raw)
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": "Continue. Make sure your JSON is valid."})
            continue

        if "final_response" in parsed:
            yield ("final", parsed["final_response"])
            return

        if "tool" in parsed:
            tool_name = parsed.get("tool")
            tool_args = parsed.get("args", {})

            yield ("tool_call", {"tool": tool_name, "args": tool_args})

            if tool_name not in TOOLS:
                tool_result = {"error": f"Unknown tool: {tool_name}"}
            else:
                try:
                    tool_result = TOOLS[tool_name](**tool_args)
                except Exception as e:
                    tool_result = {"error": str(e)}

            yield ("tool_result", {"tool": tool_name, "result": tool_result})

            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"Tool result for {tool_name}: {json.dumps(tool_result)}\n\nContinue your analysis."})
        else:
            yield ("thought", raw)
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": "Continue. Use a tool or provide final_response."})

    yield ("error", "Agent reached maximum iterations without a final response.")
