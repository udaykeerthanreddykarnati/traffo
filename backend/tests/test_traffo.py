"""
Unit and integration tests for Traffo backend modules.
Mocks external HTTP and Groq calls so tests run offline and deterministically.
"""

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import agent  # noqa: E402
import server  # noqa: E402
import tools  # noqa: E402
from schemas import IncidentReport, IncidentRequest, MapLocation  # noqa: E402
from validator import apply_safe_overrides, validate_report  # noqa: E402


def _mock_response(payload: dict, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _mock_groq_completion(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class TestJsonExtraction(unittest.TestCase):
    def test_extract_json_object_variants(self):
        self.assertEqual(agent.extract_json_object('{"tool": "get_weather"}'), {"tool": "get_weather"})
        fenced = 'Sure, calling tool:\n```json\n{"tool": "geocode_location", "args": {"query": "Hoskote"}}\n```'
        self.assertEqual(
            agent.extract_json_object(fenced),
            {"tool": "geocode_location", "args": {"query": "Hoskote"}},
        )
        prosed = 'Thinking about it... {"final_response": {"risk_score": 7}} trailing note'
        self.assertEqual(agent.extract_json_object(prosed), {"final_response": {"risk_score": 7}})
        self.assertIsNone(agent.extract_json_object("no json here at all"))
        self.assertIsNone(agent.extract_json_object("{invalid json}"))


class TestTools(unittest.TestCase):
    def test_get_traffic_flow_missing_key(self):
        with patch.dict(os.environ, {"TOMTOM_API_KEY": ""}, clear=False):
            res = tools.get_traffic_flow(12.97, 77.59, "Bengaluru")
            self.assertFalse(res["available"])
            self.assertIn("TOMTOM_API_KEY", res["error"])

    def test_get_traffic_flow_live_and_closure(self):
        slow_payload = {
            "flowSegmentData": {
                "frc": "FRC0",
                "currentSpeed": 18,
                "freeFlowSpeed": 80,
                "currentTravelTime": 120,
                "freeFlowTravelTime": 30,
                "confidence": 0.96,
                "roadClosure": False,
            }
        }
        with patch.dict(os.environ, {"TOMTOM_API_KEY": "test-key"}, clear=False):
            with patch("tools.requests.get", return_value=_mock_response(slow_payload)) as mock_get:
                res = tools.get_traffic_flow(12.9716, 77.5946, "NH48")
                self.assertTrue(res["available"])
                self.assertEqual(res["road_class"], "Motorway / Expressway")
                self.assertEqual(res["speed_ratio"], 0.23)
                self.assertEqual(res["delay_seconds"], 90)
                self.assertEqual(res["flow_congestion_level"], "critical")
                self.assertFalse(res["road_closure"])
                called_params = mock_get.call_args.kwargs["params"]
                self.assertEqual(called_params["point"], "12.9716,77.5946")

        closed_payload = {
            "flowSegmentData": {
                "frc": "FRC1",
                "currentSpeed": 45,
                "freeFlowSpeed": 60,
                "currentTravelTime": 40,
                "freeFlowTravelTime": 30,
                "confidence": 1.0,
                "roadClosure": True,
            }
        }
        with patch.dict(os.environ, {"TOMTOM_API_KEY": "test-key"}, clear=False):
            with patch("tools.requests.get", return_value=_mock_response(closed_payload)):
                res = tools.get_traffic_flow(12.9716, 77.5946)
                self.assertTrue(res["road_closure"])
                self.assertEqual(res["flow_congestion_level"], "critical")

    def test_get_nearby_incidents_bbox_and_parsing(self):
        with patch.dict(os.environ, {"TOMTOM_API_KEY": ""}, clear=False):
            res = tools.get_nearby_incidents(12.97, 77.59)
            self.assertFalse(res["available"])
            self.assertEqual(res["incidents"], [])

        tomtom_v5_payload = {
            "incidents": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[77.590, 12.970], [77.596, 12.974], [77.600, 12.978]],
                    },
                    "properties": {
                        "id": "inc-1",
                        "iconCategory": 1,
                        "magnitudeOfDelay": 3,
                        "events": [{"description": "Multi-vehicle accident", "code": 101}],
                        "startTime": "2026-11-21T12:00:00Z",
                        "from": "Silk Board",
                        "to": "HSR Layout",
                        "length": 600,
                        "delay": 720,
                        "roadNumbers": ["ORR"],
                    },
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [77.585, 12.968]},
                    "properties": {
                        "id": "inc-2",
                        "iconCategory": 9,
                        "magnitudeOfDelay": 1,
                        "events": [{"description": "Roadworks"}],
                        "delay": 120,
                    },
                },
            ]
        }
        with patch.dict(os.environ, {"TOMTOM_API_KEY": "test-key"}, clear=False):
            with patch("tools.requests.get", return_value=_mock_response(tomtom_v5_payload)) as mock_get:
                res = tools.get_nearby_incidents(12.97, 77.59, radius_km=3.0)
                self.assertTrue(res["available"])
                self.assertEqual(res["total_found"], 2)
                self.assertEqual(len(res["incidents"]), 2)

                # Verify bbox format is minLon,minLat,maxLon,maxLat
                bbox_parts = [float(x) for x in mock_get.call_args.kwargs["params"]["bbox"].split(",")]
                min_lon, min_lat, max_lon, max_lat = bbox_parts
                self.assertLess(min_lon, 77.59)
                self.assertGreater(max_lon, 77.59)
                self.assertLess(min_lat, 12.97)
                self.assertGreater(max_lat, 12.97)

                top = res["incidents"][0]
                self.assertEqual(top["id"], "inc-1")
                self.assertEqual(top["category"], "accident")
                self.assertEqual(top["delay_magnitude"], "major")
                # Midpoint of LineString flipped from [lon, lat] -> (lat, lon)
                self.assertAlmostEqual(top["lat"], 12.974)
                self.assertAlmostEqual(top["lon"], 77.596)

    def test_geocode_location_nominatim_and_fallback(self):
        nom_payload = [{"lat": "12.9177", "lon": "77.6238", "display_name": "Silk Board, Bengaluru, India"}]
        with patch("tools.requests.get", return_value=_mock_response(nom_payload)):
            res = tools.geocode_location("Silk Board, Bengaluru")
            self.assertTrue(res["found"])
            self.assertEqual(res["source"], "nominatim")
            self.assertAlmostEqual(res["lat"], 12.9177)

        # Fallback when Nominatim returns empty
        om_payload = {"results": [{"name": "Hoskote", "admin1": "Karnataka", "country": "India", "latitude": 13.07, "longitude": 77.79}]}
        with patch("tools.requests.get", side_effect=[_mock_response([]), _mock_response(om_payload)]):
            res = tools.geocode_location("Hoskote")
            self.assertTrue(res["found"])
            self.assertEqual(res["source"], "open-meteo")
            self.assertAlmostEqual(res["lat"], 13.07)


class TestValidator(unittest.TestCase):
    def _base_report(self, **overrides):
        data = {
            "congestion_level": "low",
            "risk_score": 3,
            "primary_cause": "Vehicle breakdown",
            "affected_area": "NH48",
            "recommended_actions": ["Dispatch patrol unit"],
            "public_broadcast": "Minor delay on NH48.",
            "reasoning_summary": "Low severity test report.",
            "confidence": "medium",
        }
        data.update(overrides)
        return IncidentReport(**data)

    def test_validator_catches_telemetry_and_text_violations(self):
        report = self._base_report(congestion_level="low", risk_score=3)
        telemetry = {
            "traffic_flow": {"available": True, "road_closure": True, "speed_ratio": 0.1},
            "weather": {"available": True, "hazardous": True, "condition": "Thunderstorm"},
            "nearby_incidents": {"available": True, "incidents": [{"delay_magnitude": "major", "category": "accident"}]},
        }
        val = validate_report(report, telemetry, "Major crash 6pm rush hour, 3 lanes blocked.")
        self.assertFalse(val["valid"])
        self.assertEqual(val["min_congestion_level"], "critical")
        self.assertEqual(val["min_risk_score"], 8)
        self.assertGreaterEqual(len(val["violations"]), 4)

        adjusted = apply_safe_overrides(report, val)
        self.assertEqual(adjusted.congestion_level, "critical")
        self.assertEqual(adjusted.risk_score, 8)

    def test_validator_passes_consistent_report(self):
        report = self._base_report(congestion_level="high", risk_score=8)
        telemetry = {
            "traffic_flow": {"available": True, "road_closure": False, "speed_ratio": 0.22},
            "weather": {"available": True, "hazardous": True, "condition": "Rain showers"},
            "nearby_incidents": {"available": True, "incidents": []},
        }
        val = validate_report(report, telemetry, "Crash at 6pm rush hour. 3 lanes blocked.")
        self.assertTrue(val["valid"])
        self.assertEqual(val["violations"], [])


class TestAgentLoop(unittest.TestCase):
    def test_pinned_prefetch_and_validation_retry(self):
        invalid_final = json.dumps({
            "final_response": {
                "congestion_level": "low",
                "risk_score": 2,
                "primary_cause": "Collision",
                "affected_area": "NH48 Hoskote",
                "recommended_actions": ["Dispatch patrol"],
                "public_broadcast": "Minor traffic on NH48.",
                "reasoning_summary": "Underestimated initially.",
                "confidence": "medium",
            }
        })
        corrected_final = json.dumps({
            "final_response": {
                "congestion_level": "high",
                "risk_score": 8,
                "primary_cause": "Multi-lane collision in heavy rain",
                "affected_area": "NH48 Hoskote",
                "recommended_actions": ["Dispatch patrol and EMS", "Verify via CCTV"],
                "public_broadcast": "Major accident on NH48 near Hoskote. Heavy delays; avoid corridor.",
                "reasoning_summary": "Live TomTom speed drop and hazardous rain confirm high severity.",
                "confidence": "high",
            }
        })

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            _mock_groq_completion(invalid_final),
            _mock_groq_completion(corrected_final),
        ]

        weather_res = {"available": True, "condition": "Rain showers", "temperature_c": 22, "visibility_km": 4.0, "hazardous": True}
        flow_res = {"available": True, "road_class": "Motorway / Expressway", "current_speed_kmh": 15, "free_flow_speed_kmh": 80, "speed_ratio": 0.19, "delay_seconds": 240, "road_closure": False, "flow_congestion_level": "critical"}
        nearby_res = {"available": True, "total_found": 1, "incidents": [{"id": "1", "category": "accident", "delay_magnitude": "major"}]}

        with patch("agent._get_groq_client", return_value=mock_client), \
             patch("agent.get_weather", return_value=weather_res), \
             patch("agent.get_traffic_flow", return_value=flow_res), \
             patch("agent.get_nearby_incidents", return_value=nearby_res):
            events = list(agent.run_agent(
                "Major accident on NH48 near Hoskote. 6pm rush hour. 3 lanes blocked.",
                pinned_location={"lat": 13.07, "lon": 77.79, "label": "NH48 Hoskote"},
            ))

        types = [t for t, _ in events]
        self.assertEqual(types.count("tool_call"), 3)
        self.assertEqual(types.count("validation"), 2)
        self.assertEqual(types[-1], "final")

        final_payload = events[-1][1]
        self.assertEqual(final_payload["congestion_level"], "high")
        self.assertEqual(final_payload["risk_score"], 8)
        self.assertTrue(final_payload["validation"]["passed"])
        self.assertFalse(final_payload["validation"]["auto_adjusted"])
        self.assertGreaterEqual(len(final_payload["validation"]["violations_caught"]), 1)
        self.assertNotIn("signal_adjustments", final_payload)

    def test_escalation_hard_stop(self):
        escalate_call = json.dumps({
            "tool": "escalate_to_human",
            "args": {
                "reason": "Unknown location and no severity details",
                "situation_summary": "Unspecified road incident",
            },
        })
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_groq_completion(escalate_call)

        with patch("agent._get_groq_client", return_value=mock_client):
            events = list(agent.run_agent("Something happened somewhere earlier."))

        self.assertEqual(mock_client.chat.completions.create.call_count, 1)
        self.assertEqual(events[-1][0], "final")
        final_payload = events[-1][1]
        self.assertTrue(final_payload["escalated"])
        self.assertEqual(final_payload["congestion_level"], "unknown")
        self.assertIsNone(final_payload["risk_score"])
        self.assertTrue(final_payload["escalation_details"]["handoff_id"].startswith("TRF-"))
        self.assertNotIn("signal_adjustments", final_payload)


class TestServerEndpoints(unittest.TestCase):
    def test_root_health_examples_and_sse_stream(self):
        self.assertEqual(server.root()["version"], "2.0.0")
        with patch.dict(os.environ, {"GROQ_API_KEY": "g-key", "TOMTOM_API_KEY": ""}, clear=False):
            h = server.health()
            self.assertTrue(h["healthy"])
            self.assertTrue(h["providers"]["groq"])
            self.assertFalse(h["providers"]["tomtom"])

        ex = server.get_examples()
        self.assertGreaterEqual(len(ex["examples"]), 3)

        req = IncidentRequest(
            incident="Crash at Silk Board",
            location=MapLocation(lat=12.9177, lon=77.6238, label="Silk Board"),
        )
        fake_events = [
            ("thought", "Checking telemetry"),
            ("final", {"congestion_level": "high", "risk_score": 8}),
        ]
        with patch("server.run_agent", return_value=iter(fake_events)) as mock_run:
            resp = server.analyze_incident(req)

            async def _collect():
                out = []
                async for chunk in resp.body_iterator:
                    out.append(chunk)
                return out

            chunks = asyncio.run(_collect())
            mock_run.assert_called_once_with(
                "Crash at Silk Board",
                pinned_location={"lat": 12.9177, "lon": 77.6238, "label": "Silk Board"},
            )
            joined = "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)
            self.assertIn('"type": "thought"', joined)
            self.assertIn('"type": "final"', joined)
            self.assertIn('"type": "done"', joined)


if __name__ == "__main__":
    unittest.main()
