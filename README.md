# Traffo
### Agentic Traffic Incident Response System

Traffo converts a plain-English description of a traffic incident into a structured, actionable response plan — autonomously gathering whatever real-world information it needs along the way.

You type something like:

> "Major accident on NH48 near Hoskote, Bengaluru. 6pm rush hour. Heavy rain. 3 lanes blocked."

And Traffo decides for itself what to check, checks it, reasons about what it found, and returns a structured incident report with congestion level, risk score, recommended actions, and a public broadcast message.

---

## Why This Is an Agent, Not a Pipeline

A typical LLM tool runs one fixed sequence: input → process → output. Traffo doesn't work that way.

Given an incident, Traffo decides at runtime what it needs to know — live weather, nearby news of related incidents, alternate routes — and calls the relevant tool only when it determines that information is missing. It observes each result, reasons about it, and decides what to check next. This reason → act → observe loop repeats until the model is confident enough to produce a final structured decision.

Before finalizing anything, Traffo runs its own decision through a rule-based validator that checks for logical inconsistencies — for example, catching cases where the model rates a scenario as "low risk" despite heavy rain and multiple blocked lanes. This validation layer exists specifically to catch and correct LLM reasoning errors before they reach the output.

If a situation is too ambiguous or conflicting, Traffo can escalate to a human operator with a structured handoff instead of guessing.

---

## How It Works

```
Natural language incident
        │
        ▼
┌────────────────────────────────────┐
│           Traffo Agent              │
│   (reason → act → observe loop)     │
└────────────────────────────────────┘
        │
   Tools available to the agent:
   ├── get_weather              live weather conditions
   ├── get_alternate_routes     route options & congestion estimates
   ├── search_traffic_news      recent related incidents nearby
   ├── validate_decision        rule-based consistency check
   └── escalate_to_human        structured handoff for ambiguous cases
        │
        ▼
Structured response:
congestion_level · risk_score · primary_cause · affected_area
recommended_actions · signal_adjustments · public_broadcast
reasoning_summary · confidence
```

The agent runs this loop for up to several iterations, calling whichever tools it judges necessary, until it produces a validated final answer.

---

## Project Structure

```
traffo/
├── backend/
│   ├── agent.py          # Core agent loop and all tool implementations
│   ├── server.py         # FastAPI server, streams agent reasoning live
│   └── requirements.txt
├── frontend/
│   └── index.html        # UI showing real-time agent reasoning trace
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

## Design Notes

- The agent loop is hand-written rather than built on a framework like LangChain or LangGraph, by design — to make every part of the reasoning and tool-calling process explicit and inspectable.
- The rule-based validation layer is the system's main safeguard against LLM hallucination: it doesn't try to make the model more accurate through prompting alone, it independently checks the model's output against fixed traffic-logic constraints after the fact.
- Weather, news, and routing tools default to free, keyless APIs wherever possible so the system runs with minimal setup.
