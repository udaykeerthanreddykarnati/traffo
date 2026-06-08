"""
Traffo FastAPI Server
Streams agent reasoning steps to the frontend via Server-Sent Events (SSE)
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import json
import sys
import os

sys.path.append(os.path.dirname(__file__))
from agent import run_agent

app = FastAPI(title="Traffo API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class IncidentRequest(BaseModel):
    incident: str


@app.get("/")
def root():
    return {"status": "Traffo is running", "version": "1.0.0"}


@app.get("/health")
def health():
    return {"healthy": True}


@app.post("/analyze")
def analyze_incident(request: IncidentRequest):
    """
    Stream agent reasoning as Server-Sent Events.
    Frontend receives real-time updates as the agent thinks and uses tools.
    """
    def generate():
        try:
            for event_type, content in run_agent(request.incident):
                payload = json.dumps({"type": event_type, "content": content})
                yield f"data: {payload}\n\n"
        except Exception as e:
            error_payload = json.dumps({"type": "error", "content": str(e)})
            yield f"data: {error_payload}\n\n"
        finally:
            yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/examples")
def get_examples():
    """Sample incidents to demo the system."""
    return {
        "examples": [
            "Major accident on NH48 near Hoskote, Bengaluru. 6pm rush hour. Heavy rain. 3 lanes blocked.",
            "Flash floods reported near Silk Board junction, Bengaluru. Metro services also delayed.",
            "Multi-vehicle pile-up on ORR near Marathahalli. Morning peak hour. Foggy conditions.",
            "Road cave-in near Guindy, Chennai. Emergency vehicles need corridor. Evening traffic.",
            "Protest march on MG Road blocking all lanes. Weekend afternoon. Clear weather.",
        ]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
