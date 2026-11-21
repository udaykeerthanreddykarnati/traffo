"""
Pydantic v2 request and response schemas for Traffo.
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


class MapLocation(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    label: str = ""


class IncidentRequest(BaseModel):
    incident: str = Field(..., min_length=1, max_length=2000)
    location: Optional[MapLocation] = None


class IncidentReport(BaseModel):
    congestion_level: Literal["low", "medium", "high", "critical", "unknown"]
    risk_score: Optional[int] = Field(default=None, ge=1, le=10)
    primary_cause: str
    affected_area: str
    recommended_actions: list[str] = Field(default_factory=list)
    public_broadcast: str
    reasoning_summary: str
    confidence: Literal["low", "medium", "high"]
    escalated: bool = False
    escalation_details: Optional[dict] = None
    telemetry_summary: Optional[dict] = None
    validation: Optional[dict] = None

    @field_validator("congestion_level", "confidence", mode="before")
    @classmethod
    def _normalize_enum_str(cls, v):
        if isinstance(v, str):
            return v.strip().lower()
        return v
