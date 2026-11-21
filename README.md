# Traffo
### AI-Assisted Traffic Incident Triage & Dispatch Copilot

Traffo converts a natural-language traffic incident report into a structured dispatch triage ticket and draft motorist broadcast — enriched with live **TomTom Traffic Flow**, **TomTom Incident Details**, and **Open-Meteo Weather** telemetry, and protected by a mandatory deterministic Python guardrail layer.

> **Scope note:** Traffo is designed as a **human-in-the-loop dispatch copilot** (incident enrichment, severity triage, and draft advisory generation). It does **not** autonomously control traffic signals or replace city TMC control systems.

---

## Architecture

```
Natural-language report (+ optional map pin)
        │
        ▼
┌────────────────────────────────────────────────────────┐
│               Traffo Agent (ReAct Loop)                │
│   Llama 3.3 70B via Groq · Custom Python orchestration │
└────────────────────────────────────────────────────────┘
        │
        ├── geocode_location       Nominatim + Open-Meteo fallback
        ├── get_weather            Open-Meteo live conditions & visibility
        ├── get_traffic_flow       TomTom Flow v4 (speed, free-flow, closure, FRC)
        ├── get_nearby_incidents   TomTom Incident Details v5 (active jams/closures)
        └── escalate_to_human      Terminal handoff — hard stop in control flow
        │
        ▼
┌────────────────────────────────────────────────────────┐
│      Mandatory Deterministic Validator (Python)        │
│  Pydantic v2 schema + telemetry consistency rules      │
│  (auto-retries LLM on violation, clamps bounds if max) │
└────────────────────────────────────────────────────────┘
        │
        ▼
Structured Triage Output (streamed via FastAPI SSE):
congestion_level · risk_score · telemetry_summary · validation
recommended_actions · public_broadcast (draft) · reasoning_summary
```

---

## Key Engineering Features

- **Real-time TomTom Traffic Telemetry:** Queries TomTom Flow Segment Data v4 for live current vs. free-flow speed, delay, road class (`FRC0`–`FRC6`), and road closures, plus TomTom Incident Details v5 for active nearby incidents (plotted directly on the Leaflet map).
- **Deterministic Pre-Fetch for Map Pins:** When an operator pins or searches a location on the map, coordinates are sent structurally to `/analyze` and weather, traffic flow, and nearby incidents are fetched in Python *before* the LLM starts reasoning.
- **Mandatory Post-Generation Guardrails:** Every `final_response` is parsed with Pydantic v2 and checked by [`backend/validator.py`](backend/validator.py) against the verified TomTom + Open-Meteo telemetry actually fetched (not LLM-supplied numbers). Violations trigger an automated self-correction retry before delivery (or safe bound clamping if retries exhaust).
- **Terminal Escalation Hard Stop:** Calling `escalate_to_human` terminates the loop immediately in Python so the model cannot fabricate a confident report after flagging an unresolvable incident.
- **XSS-Safe Streaming UI:** [`frontend/index.html`](frontend/index.html) renders SSE traces, telemetry cards, and TomTom map popups with `textContent` DOM nodes instead of untrusted `innerHTML`.

---

## Project Structure

```
traffo/
├── backend/
│   ├── agent.py              # Custom ReAct loop, JSON parser, telemetry pre-fetch, retry loop
│   ├── schemas.py            # Pydantic v2 request/response models
│   ├── server.py             # FastAPI server + SSE streaming (/analyze, /health, /examples)
│   ├── tools.py              # Nominatim/Open-Meteo + TomTom Flow v4 & Incident Details v5
│   ├── validator.py          # Deterministic rule-based post-generation validator
│   ├── requirements.txt
│   └── tests/
│       └── test_traffo.py    # Offline unittest suite (mocked HTTP + Groq)
├── frontend/
│   └── index.html            # Map picker, live TomTom incident markers, SSE trace & triage UI
├── .env.example
└── README.md
```

---

## Tech Stack

| Component | Technology |
|---|---|
| LLM | Groq — Llama 3.3 70B (`llama-3.3-70b-versatile`) |
| Agent loop | Custom Python ReAct loop (no orchestration framework) |
| Live traffic flow & incidents | TomTom Traffic Flow v4 + Incident Details v5 (free tier: 2,500 req/day) |
| Weather | Open-Meteo Forecast API (free, no key) |
| Map & geocoding | Leaflet.js + OpenStreetMap + Nominatim |
| Guardrails & schemas | Pydantic v2 + deterministic Python validator |
| Backend | FastAPI + Server-Sent Events (SSE) |
| Frontend | Vanilla HTML / CSS / JavaScript |

---

## Setup & Running Locally

```bash
cp .env.example .env
# Add GROQ_API_KEY (required) and TOMTOM_API_KEY (recommended for live flow & incidents)

cd backend
pip install -r requirements.txt
python3 server.py
```

Open `frontend/index.html` in a browser (or serve it with `python3 -m http.server 3000` inside `frontend/`).

### Running Tests

```bash
python3 -m unittest discover -s backend/tests -v
```

---

## Design Notes & Removed Features

Earlier iterations included DuckDuckGo HTML scraping for news, OpenRouteService static routing with hardcoded mock fallbacks, and LLM-generated `signal_adjustments`. All three were intentionally removed:
- **News scraping** lagged live crashes by 30–120 minutes and broke on HTML changes; replaced by **TomTom Incident Details v5**.
- **Static / mock alternate routes** gave no live congestion visibility; replaced by **TomTom Flow v4** live speed ratios and road-closure telemetry.
- **LLM `signal_adjustments`** are ungrounded without intersection phase controllers; reframed as **operator dispatch playbook actions** and **draft public broadcasts** pending human sign-off.
