"""A deliberately small web UI for the demonstrator.

Standard library only: no framework, no build step, no package manager. Start
it with ``mission-machine serve`` and open the printed address.

The UI has four screens - MISSION, CONFIGURE, COMPARE, OPERATE - and holds one
operations session in memory. That is enough for a demonstration and keeps the
interesting part of the system in the domain modules rather than in the web
layer.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from mission_machine import __version__
from mission_machine.evidence.labels import DEMONSTRATOR_DISCLAIMER
from mission_machine.explainability.assumptions import as_dicts
from mission_machine.explainability.explain import build_recommendation, comparison_table
from mission_machine.mission.library import DEFAULT_MISSION_ID, list_missions, load_mission
from mission_machine.operations.session import OperationsSession
from mission_machine.planning.engine import PlanningEngine
from mission_machine.planning.engine import DEFAULT_STRATEGIES, Strategy
from mission_machine.resilience.failures import (
    GENERATOR_B_AND_GRID_LOSS,
    GENERATOR_B_UNAVAILABLE,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Every route that changes session state. All of them plan, assess or record a
#: decision; none of them commands an asset, and nothing may be added here that
#: does. Checked by ``tests/test_no_control_path.py``.
MUTATING_ROUTES: tuple[str, ...] = (
    "/api/configure",
    "/api/select",
    "/api/advance",
    "/api/degrade",
    "/api/accept-premise",
    "/api/dismiss-premise",
    "/api/handover",
    "/api/reset",
)

SCENARIOS = {
    GENERATOR_B_UNAVAILABLE.scenario_id: GENERATOR_B_UNAVAILABLE,
    GENERATOR_B_AND_GRID_LOSS.scenario_id: GENERATOR_B_AND_GRID_LOSS,
}

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
}


class AppState:
    """One in-memory operations session, plus the last plan and recommendation."""

    def __init__(self, mission_id: str = DEFAULT_MISSION_ID) -> None:
        self.lock = threading.Lock()
        self.mission_id = mission_id
        self.reset(mission_id)

    def reset(self, mission_id: str | None = None, world: str | None = None) -> None:
        self.mission_id = mission_id or self.mission_id
        mission = load_mission(self.mission_id)
        engine = PlanningEngine(mission)
        self.world = world or "as forecast"
        realised = None
        if self.world != "as forecast":
            windows = engine.environment.grid.available_windows
            surviving = [list(windows[0])]
            if self.world != "grid never returns":
                delay = float(self.world.split()[1])
                surviving.append([windows[1][0] + delay, windows[1][1]])
            realised = engine.environment.with_grid_windows(
                surviving, name="observed", note=f"Host-nation supply: {self.world}."
            )
        self.session = OperationsSession(mission, engine, realised_environment=realised)
        self.plan = None
        self.recommendation = None
        self.report = None

    # -- payload builders ---------------------------------------------------

    def mission_payload(self) -> dict[str, Any]:
        mission = self.session.mission
        environment = self.session.engine.environment
        hours = mission.hours
        return {
            "mission": mission.to_dict(),
            "validation": mission.validate(),
            "assets": mission.inventory.to_dict(),
            "environment": environment.to_dict(),
            "derived": {
                "mean_critical_demand_kw": round(mission.mean_critical_demand_kw(environment), 2),
                "critical_energy_demand_kwh": round(
                    mission.critical_energy_demand_kwh(environment), 1
                ),
                "grid_availability_fraction": round(
                    environment.grid.availability_fraction(mission.mission_duration_h), 3
                ),
                "mobility_excluded_assets": mission.mobility_excluded_assets(),
            },
            "profiles": {
                "hours": hours,
                "ambient_c": [round(environment.temperature_c(h), 2) for h in hours],
                "solar_fraction": [round(environment.solar_fraction(h), 3) for h in hours],
                "grid_available": [environment.grid_available_at(h) for h in hours],
                "critical_demand_kw": [
                    round(mission.critical_demand_kw(h, environment.temperature_c(h)), 2)
                    for h in hours
                ],
                "secondary_demand_kw": [
                    round(
                        sum(
                            load.demand_kw(h, environment.temperature_c(h))
                            for load in mission.secondary_load_assets()
                        ),
                        2,
                    )
                    for h in hours
                ],
            },
            "available_missions": list_missions(),
            "worlds": ["as forecast", "grid 4 h late", "grid 8 h late", "grid never returns"],
            "current_world": self.world,
            "scenarios": [
                {
                    "scenario_id": scenario.scenario_id,
                    "name": scenario.name,
                    "description": scenario.description,
                }
                for scenario in SCENARIOS.values()
            ],
            "assumptions": as_dicts(),
            "disclaimer": DEMONSTRATOR_DISCLAIMER,
            "version": __version__,
        }

    def plan_payload(self, include_steps: bool = False) -> dict[str, Any]:
        if self.plan is None:
            return {"plan": None}
        return {
            "plan": self.plan.to_dict(include_steps=include_steps),
            "comparison": comparison_table(self.plan.options),
            "recommendation": self.recommendation.to_dict() if self.recommendation else None,
            "status": self.session.status_dict(),
        }

    # -- actions ------------------------------------------------------------

    def configure(self, strategies: list[str] | None = None, sensitivity: bool = True) -> dict[str, Any]:
        chosen = (
            tuple(Strategy(name.upper()) for name in strategies) if strategies else DEFAULT_STRATEGIES
        )
        self.plan = self.session.generate_options(strategies=chosen)
        self.recommendation = build_recommendation(
            self.plan, self.session.engine, include_sensitivity=sensitivity
        )
        return self.plan_payload()

    def select(self, configuration_id: str, rationale: str = "") -> dict[str, Any]:
        recommended = (
            self.recommendation.recommended_configuration_id if self.recommendation else ""
        )
        self.session.select(configuration_id, rationale=rationale, recommended=recommended)
        return self.operate_payload()

    def advance(self, hour: float) -> dict[str, Any]:
        self.session.run_to(hour)
        return self.operate_payload()

    def operate_payload(self) -> dict[str, Any]:
        session = self.session
        if session.selected is None:
            return {"selected": None, "status": session.status_dict()}
        assessment = session.assess()
        result, _ = session.project()
        return {
            "selected": session.selected.to_dict(),
            "assessment": assessment.to_dict(),
            "status": session.status_dict(),
            "timeline": [step.to_dict() for step in result.steps],
            "report": self.report.to_dict() if self.report else None,
            "premises": session.check_premises().to_dict(),
            "world": self.world,
            "disclaimer": DEMONSTRATOR_DISCLAIMER,
        }

    def dismiss_premise(self, key: str, rationale: str = "") -> dict[str, Any]:
        """Record that the operator saw this and is keeping the stated premise."""

        self.session.dismiss_premise_revision(key, rationale=rationale)
        return self.operate_payload()

    def handover(self, outgoing: str = "", incoming: str = "") -> dict[str, Any]:
        """Assemble the brief for the next watch. Changes nothing about the plan."""

        brief = self.session.handover(outgoing=outgoing, incoming=incoming)
        payload = self.operate_payload()
        payload["handover"] = brief.to_dict()
        return payload

    def accept_premise(self, key: str, rationale: str = "") -> dict[str, Any]:
        self.session.accept_premise_revision(key, rationale=rationale)
        self.plan = None
        self.recommendation = None
        return self.operate_payload()

    def degrade(self, scenario_id: str) -> dict[str, Any]:
        scenario = SCENARIOS[scenario_id]
        self.report = self.session.inject(scenario)
        self.plan = self.report.options
        self.recommendation = self.report.recommendation
        payload = self.operate_payload()
        payload["report"] = self.report.to_dict()
        return payload


STATE = AppState()


class Handler(BaseHTTPRequestHandler):
    server_version = f"MissionMachine/{__version__}"

    # -- plumbing -----------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return  # keep the demo console clean

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _static(self, name: str) -> None:
        path = (STATIC_DIR / name).resolve()
        if not path.is_file() or STATIC_DIR not in path.parents:
            self._json({"error": "not found"}, 404)
            return
        content_type = _CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        self._send(200, path.read_bytes(), content_type)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    # -- routes -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        route = url.path
        query = parse_qs(url.query)
        try:
            if route in ("/", "/index.html"):
                self._static("index.html")
            elif route.startswith("/static/"):
                self._static(route[len("/static/") :])
            elif route == "/api/mission":
                with STATE.lock:
                    self._json(STATE.mission_payload())
            elif route == "/api/plan":
                with STATE.lock:
                    self._json(STATE.plan_payload(include_steps="steps" in query))
            elif route == "/api/operate":
                with STATE.lock:
                    self._json(STATE.operate_payload())
            elif route == "/api/assumptions":
                self._json({"assumptions": as_dicts()})
            else:
                self._json({"error": f"unknown route {route}"}, 404)
        except Exception as exc:  # pragma: no cover - demo robustness
            self._json({"error": str(exc), "type": type(exc).__name__}, 500)

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route not in MUTATING_ROUTES:
            self._json({"error": f"unknown route {route}"}, 404)
            return
        body = self._body()
        try:
            with STATE.lock:
                if route == "/api/configure":
                    self._json(
                        STATE.configure(
                            strategies=body.get("strategies"),
                            sensitivity=body.get("sensitivity", True),
                        )
                    )
                elif route == "/api/select":
                    self._json(
                        STATE.select(body["configuration_id"], body.get("rationale", ""))
                    )
                elif route == "/api/advance":
                    self._json(STATE.advance(float(body.get("hour", 0.0))))
                elif route == "/api/degrade":
                    self._json(STATE.degrade(body.get("scenario_id", "SC-DEGRADED-001")))
                elif route == "/api/accept-premise":
                    self._json(
                        STATE.accept_premise(body["key"], body.get("rationale", ""))
                    )
                elif route == "/api/dismiss-premise":
                    self._json(
                        STATE.dismiss_premise(body["key"], body.get("rationale", ""))
                    )
                elif route == "/api/handover":
                    self._json(
                        STATE.handover(
                            body.get("outgoing", "WATCH A"), body.get("incoming", "WATCH B")
                        )
                    )
                elif route == "/api/reset":
                    STATE.reset(body.get("mission_id"), body.get("world"))
                    self._json({"ok": True, "mission_id": STATE.mission_id, "world": STATE.world})
                else:
                    self._json({"error": f"unknown route {route}"}, 404)
        except KeyError as exc:
            self._json({"error": f"missing field: {exc}"}, 400)
        except Exception as exc:  # pragma: no cover - demo robustness
            self._json({"error": str(exc), "type": type(exc).__name__}, 500)


def serve(host: str = "127.0.0.1", port: int = 8000, mission_id: str = DEFAULT_MISSION_ID) -> None:
    global STATE
    STATE = AppState(mission_id)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(DEMONSTRATOR_DISCLAIMER)
    print()
    print(f"Mission Machine {__version__} - mission {mission_id}")
    print(f"Open http://{host}:{port}/  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print("\nstopped")
    finally:
        httpd.server_close()
