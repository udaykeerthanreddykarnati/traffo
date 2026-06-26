# Traffo
### Agentic Traffic Incident Response System

Traffo converts a plain-English description of a traffic incident into a structured, actionable response plan — autonomously gathering whatever real-world information it needs along the way.

You type something like:

> "Major accident on NH48 near Hoskote, Bengaluru. 6pm rush hour. Heavy rain. 3 lanes blocked."

— optionally pinning the exact location on a map — and Traffo decides for itself what to check, checks it, reasons about what it found, and returns a structured incident report with congestion level, risk score, recommended actions, and a public broadcast message.

---

## Why This Is an Agent, Not a Pipeline

A typical LLM tool runs one fixed sequence: input → process → output. Traffo doesn't work that way.

Given an incident, Traffo decides at runtime what it needs to know — live weather, nearby news of related incidents, alternate routes — and calls the relevant tool only when it determines that information is missing. It observes each result, reasons about it, and decides what to check next. This reason → act → observe loop repeats until the model is confident enough to produce a final structured decision.

Before finalizing anything, Traffo runs its own decision through a rule-based validator that checks for logical inconsistencies — for example, catching cases where the model rates a scenario as "low risk" despite heavy rain and multiple blocked lanes. This validation layer exists specifically to catch and correct LLM reasoning errors before they reach the output.

If a situation is too ambiguous or conflicting, Traffo escalates to a human operator with a structured handoff instead of guessing — and escalation is a hard stop in the control flow, not just another tool call the agent can talk past.

---

## How It Works

```
Natural language incident (+ optional map pin)
        │
        ▼
┌────────────────────────────────────┐
│           Traffo Agent              │
│   (reason → act → observe loop)     │
└────────────────────────────────────┘
        │
   Tools available to the agent:
   ├── get_weather               live weather by city/town name
   ├── get_weather_by_coords     live weather by exact lat/lon (map pin)
   ├── get_alternate_routes      route options & congestion estimates
   ├── search_traffic_news       recent related incidents nearby
   ├── validate_decision         rule-based consistency check
   └── escalate_to_human         structured handoff — hard stop
        │
        ▼
Structured response:
congestion_level · risk_score · primary_cause · affected_area
recommended_actions · signal_adjustments · public_broadcast
reasoning_summary · confidence
```

The agent runs this loop for up to several iterations, calling whichever tools it judges necessary, until it produces a validated final answer.

---

## Map-Based Location Input

Street names, junctions, and landmarks ("MG Road", "Silk Board junction") aren't recognized by city-level geocoding APIs, so a text-only system frequently fails to find weather data for anything more specific than a city name.

Traffo addresses this with an interactive map: search for a place or click directly on the map to pin an exact location. The pinned coordinates are attached to the incident text and detected in Python before the agent loop starts — weather for that exact point is fetched immediately and handed to the agent as a known fact, rather than relying on the model to notice and act on the coordinates itself.

---

## Project Structure

```
traffo/
├── backend/
│   ├── agent.py          # Core agent loop, tool implementations, map-coordinate handling
│   ├── server.py         # FastAPI server, streams agent reasoning live
│   └── requirements.txt
├── frontend/
│   └── index.html        # UI: incident input, map picker, live agent reasoning trace
├── .env.example
└── README.md
```

---

## Tech Stack

| Component | Technology |
|---|---|
| LLM | Groq — Llama 3.3 70B (free tier) |
| Agent loop | Custom-built, no external agent framework |
| Weather data | Open-Meteo API (free, no key required) |
| Routing | OpenRouteService (free tier) |
| News search | DuckDuckGo |
| Map + geocoding | Leaflet.js + OpenStreetMap tiles + Nominatim search (all free) |
| Decision validation | Custom Python rule engine |
| Backend | FastAPI with Server-Sent Events streaming |
| Frontend | Vanilla HTML / CSS / JavaScript |

---

## Running It

```bash
cd backend
pip install -r requirements.txt
cp ../.env.example .env
# add your GROQ_API_KEY to .env
python3 server.py
```

Then open `frontend/index.html` in a browser. The backend must be running for the frontend to work.

---

## Design Notes & Known Limitations

- The agent loop is hand-written rather than built on a framework like LangChain or LangGraph, by design — to make every part of the reasoning and tool-calling process explicit and inspectable.
- The rule-based validation layer is the system's main safeguard against LLM hallucination: it independently checks the model's output against fixed traffic-logic constraints after the fact, rather than trying to make the model more accurate through prompting alone.
- **Escalation is a hard stop.** Early testing showed the agent would call `escalate_to_human`, receive the result, and then continue reasoning anyway — sometimes fabricating a confident final answer despite having just flagged the situation as unresolvable. The control flow now treats escalation as terminal.
- **Map coordinates are handled in Python, not left to the LLM.** Testing showed the underlying model (Llama 3.3 70B on Groq's free tier) was unreliable at noticing instructions buried in a longer prompt — it would frequently ignore provided coordinates and call the name-based weather lookup instead, which fails on streets and landmarks. Rather than rely on prompt engineering alone, map coordinates are detected with a regex and the weather lookup is performed directly in Python before the agent loop begins.
- `get_weather` (the name-based tool) only recognizes cities and towns. For specific streets, junctions, or landmarks without a map pin, the agent is instructed to fall back to the surrounding city name for weather, while still using the specific name for news search and final output.
- The news search tool scrapes DuckDuckGo's HTML results directly rather than using a paid news API, so result quality and relevance can vary.
