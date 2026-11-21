"""
Traffo FastAPI Server
Streams agent reasoning steps and live telemetry to the frontend via Server-Sent Events (SSE).
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env", override=False)
load_dotenv(BASE_DIR / ".env", override=False)

sys.path.append(str(BASE_DIR))
from agent import run_agent  # noqa: E402
from schemas import IncidentRequest  # noqa: E402

app = FastAPI(title="Traffo API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"status": "Traffo is running", "version": "2.0.0"}


@app.get("/health")
def health():
    return {
        "healthy": True,
        "providers": {
            "groq": bool(os.environ.get("GROQ_API_KEY", "").strip()),
            "tomtom": bool(os.environ.get("TOMTOM_API_KEY", "").strip()),
        },
    }


@app.post("/analyze")
def analyze_incident(request: IncidentRequest):
    """
    Stream agent reasoning, tool telemetry, and deterministic validation via SSE.
    """
    pinned = request.location.model_dump() if request.location else None

    def generate():
        try:
            for event_type, content in run_agent(request.incident, pinned_location=pinned):
                payload = json.dumps({"type": event_type, "content": content})
                yield f"data: {payload}\n\n"
        except Exception as e:
            error_payload = json.dumps({"type": "error", "content": str(e)})
            yield f"data: {error_payload}\n\n"
        finally:
            yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/examples")
def get_examples():
    """Sample dispatch incidents to demo the triage copilot."""
    return {
        "examples": [
            "Major accident on NH48 near Hoskote, Bengaluru. 6pm rush hour. Heavy rain. 3 lanes blocked.",
            "Flash floods reported near Silk Board junction, Bengaluru. Traffic backed up toward HSR.",
            "Multi-vehicle pile-up on Outer Ring Road near Marathahalli, Bengaluru. Morning peak hour. Foggy conditions.",
            "Road cave-in near Guindy, Chennai. Emergency corridor needed. Evening traffic.",
            "Something happened somewhere on a road earlier today, maybe minor.",
        ]
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
