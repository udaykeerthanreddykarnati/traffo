# Traffo 🚦
### Agentic Traffic Incident Response System

Natural language in → autonomous multi-tool reasoning → structured incident response plan.

Traffo is an **agent**, not a pipeline. It decides what information it needs, calls the right tools in the right order, validates its own decisions against traffic management rules, and produces a complete response plan — all from a single sentence describing a traffic incident.

---

## Architecture

```
User Input (natural language)
        │
        ▼
  ┌─────────────────────────────────────────┐
  │           Traffo Agent (Gemini)          │
  │                                          │
  │  Reason → Pick Tool → Call → Observe    │
  │  Reason → Pick Tool → Call → Observe    │
  │  ...                                     │
  │  Validate Decision → Final Output       │
  └─────────────────────────────────────────┘
        │
   Tools Available:
   ├── get_weather()          → Open-Meteo API (free)
   ├── get_alternate_routes() → OpenRouteService (free)
   ├── search_traffic_news()  → DuckDuckGo (no key)
   ├── validate_decision()    → Rule-based validator
   └── escalate_to_human()    → Structured handoff
        │
        ▼
  Structured Output:
  congestion_level | risk_score | recommended_actions
  signal_adjustments | public_broadcast | reasoning_summary
```

---

## Quick Start

### 1. Clone and set up

```bash
git clone https://github.com/yourusername/traffo
cd traffo
```

### 2. Get your free Gemini API key

Go to: https://makersuite.google.com/app/apikey
Create a key. It's free, no credit card needed.

### 3. Set up environment

```bash
cd backend
cp ../.env.example .env
# Edit .env and paste your GEMINI_API_KEY
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Run the backend

```bash
python server.py
# Server starts at http://localhost:8000
```

### 6. Open the frontend

Open `frontend/index.html` in your browser. That's it.

---

## Example Inputs

```
Major accident on NH48 near Hoskote, Bengaluru. 6pm rush hour. Heavy rain. 3 lanes blocked.

Flash floods near Silk Board junction, Bengaluru. Metro services also delayed.

Multi-vehicle pile-up on ORR near Marathahalli. Morning peak hour. Foggy conditions.

Road cave-in near Guindy, Chennai. Emergency vehicles need corridor. Evening traffic.
```

---

## What Makes This an Agent

Most LLM systems are pipelines: input → fixed steps → output.

Traffo is an agent because:

1. **It decides what to do next.** Given an incident, it chooses which tools to call based on what it currently knows and what it still needs.
2. **It observes and adapts.** If the weather check shows thunderstorms, it adjusts its risk reasoning. If news search finds a related incident, it factors in cascade effects.
3. **It validates itself.** Before finalizing, it calls `validate_decision()` — a rule-based constraint checker that catches logical errors (e.g., "3 lanes blocked + rain cannot = low risk").
4. **It can escalate.** If the situation is too ambiguous, it produces a structured handoff to a human operator instead of guessing.

---

## Project Structure

```
traffo/
├── backend/
│   ├── agent.py          # Core agent loop + all tool implementations
│   ├── server.py         # FastAPI server with SSE streaming
│   └── requirements.txt
├── frontend/
│   └── index.html        # Single-file UI with real-time agent trace
├── .env.example          # Environment variables template
└── README.md
```

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| LLM | Google Gemini 1.5 Flash (free) |
| Agent Framework | Custom loop (no LangChain dependency) |
| Weather | Open-Meteo API (free, no key) |
| Routing | OpenRouteService (free tier) |
| News | DuckDuckGo HTML scraping |
| Validation | Python rule engine + Pydantic |
| API | FastAPI + SSE streaming |
| Frontend | Vanilla HTML/CSS/JS |

---

## Resume Description

> Built an agentic traffic incident response system using Google Gemini and FastAPI — autonomously chains live weather, routing, and news retrieval tools via a multi-step reasoning loop to generate structured incident response plans from natural language descriptions, with a rule-based validation layer that catches logical inconsistencies before finalizing decisions.

---


