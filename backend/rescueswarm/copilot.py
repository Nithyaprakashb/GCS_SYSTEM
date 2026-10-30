"""Offline mission copilot.

The default copilot is deterministic and rule-based: it reads the same state the
operator sees and answers with cited values. It has no command authority and
cannot send MAVLink messages or trigger a release. An optional local Ollama
model may be enabled for prose, but it is never required and always receives a
read-only summary.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .config import Config

SAFETY_NOTE = (
    "Advisory only. The copilot cannot arm, fly, change a mission or release a kit. "
    "Deterministic safety rules and the operator remain in control."
)


class Copilot:
    def __init__(self, config: Config) -> None:
        self.config = config

    def ask(self, question: str, context: dict[str, Any]) -> dict[str, Any]:
        question = (question or "").strip()
        if not question:
            raise ValueError("Question is empty")
        answer, sources = self._rule_based(question, context)
        engine = "rule-based"
        model_note = "Deterministic offline answer from recorded state."
        if self.config.get("copilot", "llm_enabled", default=False):
            prose = self._ollama(question, context, answer)
            if prose:
                answer = prose
                engine = "local-llm"
                model_note = "Local Ollama model; read-only mission summary. Not a safety authority."
        return {
            "question": question,
            "answer": answer,
            "sources": sources,
            "engine": engine,
            "model_note": model_note,
            "safety_note": SAFETY_NOTE,
            "generated_at": context.get("generated_at"),
        }

    # ------------------------------------------------------------- rule based
    def _rule_based(self, question: str, context: dict[str, Any]) -> tuple[str, list[str]]:
        q = question.lower()
        status = context.get("mission", {})
        fleet = context.get("fleet", {})
        vehicles = fleet.get("vehicles", [])
        survivors = context.get("survivors", {})
        kits = context.get("kits", {})
        events = context.get("events", [])
        allocations = context.get("allocations", {})
        safety = context.get("safety", {})
        video = context.get("video", {})
        preflight = context.get("preflight", {})
        sources: list[str] = []

        if any(word in q for word in ("battery", "power", "energy", "reserve")):
            lines = []
            for vehicle in vehicles:
                battery = vehicle.get("battery_pct")
                sources.append(f"fleet:{vehicle['callsign']}")
                if battery is None:
                    lines.append(f"{vehicle['callsign']}: battery unavailable")
                else:
                    state = "armed" if vehicle.get("armed") else "disarmed"
                    lines.append(f"{vehicle['callsign']}: {float(battery):.0f}% ({state})")
            reserve = self.config.get("mission", "battery_reserve_pct", default=22.0)
            return (
                "Battery state: " + "; ".join(lines) + f". Return reserve policy is {reserve:.0f}%."
                f" Values are {status.get('mode', 'UNKNOWN')} data.",
                sources,
            )

        if any(word in q for word in ("survivor", "detected", "person", "people", "victim")):
            counts = survivors.get("counts", {})
            total = survivors.get("total", 0)
            detail = ", ".join(f"{value} {key.lower()}" for key, value in sorted(counts.items())) or "none"
            sources.append("survivors")
            return (
                f"{total} survivor record(s): {detail}. Duplicate merge distance is "
                f"{survivors.get('merge_distance_m', 5)} m. Every report is re-validated by the GCS before allocation.",
                sources,
            )

        if any(word in q for word in ("allocat", "rationale", "why this", "assigned to")):
            assignments = allocations.get("assignments", [])
            if not assignments:
                return (
                    "No survivor allocation exists yet. Allocation runs only after a survivor report is validated "
                    "and a delivery aircraft has a free kit, sufficient battery and a healthy link.",
                    ["allocations"],
                )
            latest = assignments[-3:]
            lines = []
            for assignment in latest:
                decision = assignment.get("decision") or {}
                candidates = decision.get("candidates") or []
                rejections = decision.get("rejections") or []
                selected = decision.get("selected", assignment.get("callsign"))
                detail = (
                    f"{assignment.get('survivor_id')} → {assignment.get('callsign')} "
                    f"with {assignment.get('kit_id')} at ETA {float(assignment.get('eta_s') or 0):.0f} s"
                )
                if candidates:
                    times = ", ".join(f"{c.get('callsign')} {float(c.get('eta_s') or 0):.0f} s" for c in candidates)
                    detail += f" (candidates: {times})"
                if rejections:
                    rejected = ", ".join(str(item.get("callsign", "?")) for item in rejections[:3] if isinstance(item, dict))
                    if rejected:
                        detail += f"; rejected {rejected}"
                detail += f"; selected {selected}: {decision.get('selected_reason', 'lowest eligible ETA')}"
                lines.append(detail)
            sources.append("allocations")
            return (
                "Latest allocation decisions — " + " | ".join(lines) + ". "
                f"Policy: {str(allocations.get('rule', 'eligibility filter, then shortest queue-aware ETA')).rstrip('.')}. "
                "Tie-break uses more free kits, then the lower callsign. Allocation is deterministic and logged.",
                sources,
            )

        if any(word in q for word in ("risk", "summary", "overview", "picture")):
            vehicles_list = context.get("fleet", {}).get("vehicles", [])
            connected = sum(1 for vehicle in vehicles_list if vehicle.get("connected"))
            low = [
                f"{vehicle.get('callsign')} {float(vehicle.get('battery_pct')):.0f}%"
                for vehicle in vehicles_list
                if vehicle.get("battery_pct") is not None and float(vehicle.get("battery_pct")) < 30
            ]
            separations = safety.get("separations") or []
            counts = survivors.get("counts", {})
            unserved = counts.get("UNSERVED", 0)
            unknown = counts.get("UNKNOWN", 0)
            lines = [
                f"Mission {status.get('phase', 'SETUP')} in {status.get('mode', 'UNKNOWN')}; "
                f"link {connected}/{len(vehicles_list) or 3}, video live {video.get('live_count', 0)}/3",
                f"survivors {survivors.get('total', 0)} ({unserved} unserved, {unknown} unconfirmed), "
                f"kits free {kits.get('total_free', 0)}/{kits.get('total', 0)}",
            ]
            if low:
                lines.append("battery below 30%: " + ", ".join(low))
            if safety.get("pad", {}).get("violation"):
                lines.append("pad interlock violation: more than one armed aircraft on the pad")
            if separations:
                lines.append(f"{len(separations)} proximity separation(s) below 8 m")
            boundary_warnings = [
                callsign for callsign, boundary in (safety.get("boundaries") or {}).items()
                if isinstance(boundary, dict) and boundary.get("available") and boundary.get("warning")
            ]
            if boundary_warnings:
                lines.append("near inclusion boundary: " + ", ".join(boundary_warnings))
            degraded = [
                f"{feed.get('callsign')} {feed.get('status')}"
                for feed in video.get("feeds", [])
                if feed.get("status") not in {"LIVE", "RECORDING"}
            ]
            if degraded:
                lines.append("video degraded: " + ", ".join(degraded))
            sources.extend(["mission.status", "fleet", "safety", "survivors", "kits", "video"])
            return "Risk summary — " + "; ".join(lines) + ". " + SAFETY_NOTE, sources

        if any(word in q for word in ("block", "prevent", "cannot deliver", "not delivering", "release")):
            blockers: list[str] = []
            last_preflight = preflight.get("last") or {}
            for check in last_preflight.get("checks", []):
                if check.get("result") == "FAIL":
                    blockers.append(f"preflight {check.get('title') or check.get('check_id')}: {check.get('measured') or check.get('detail')}")
            for item in (status.get("readiness") or {}).get("blocking", []):
                blockers.append(f"readiness: {item}")
            if status.get("phase") not in {"READY", "EXECUTING", "COMPLETE"}:
                blockers.append(f"mission phase is {status.get('phase')} — release is latched until launch authority")
            if kits.get("total_free", 0) == 0:
                blockers.append("no free kits remain in the ledger")
            for vehicle in context.get("fleet", {}).get("vehicles", []):
                if vehicle.get("role") != "DELIVERY":
                    continue
                if not vehicle.get("connected"):
                    blockers.append(f"{vehicle.get('callsign')} link is down")
                elif vehicle.get("stale"):
                    blockers.append(f"{vehicle.get('callsign')} telemetry is stale")
                battery = vehicle.get("battery_pct")
                if battery is not None and float(battery) < 22:
                    blockers.append(f"{vehicle.get('callsign')} battery {float(battery):.0f}% is below the 22% return reserve")
            if safety.get("pad", {}).get("violation"):
                blockers.append("pad interlock blocks arming")
            if not blockers:
                return (
                    "No deterministic blocker is currently recorded. Release still requires hover verification, "
                    "inside-fence position, an unambiguous kit reservation and a fresh break-beam confirmation.",
                    ["mission.status", "preflight", "kits", "safety"],
                )
            sources.extend(["mission.status", "preflight", "kits", "safety", "fleet"])
            return "Delivery blockers — " + "; ".join(blockers[:8]) + ".", sources

        if any(word in q for word in ("kit", "payload", "ledger", "delivery")):
            counts = kits.get("counts", {})
            free = kits.get("total_free", 0)
            total = kits.get("total", 0)
            detail = ", ".join(f"{value} {key}" for key, value in sorted(counts.items())) or "no kits initialised"
            sources.append("kits")
            return (
                f"Kit ledger: {free}/{total} free ({detail}). A kit is DELIVERED only with CONFIRMED "
                "break-beam evidence; missing evidence becomes UNKNOWN and is never auto-retried.",
                sources,
            )

        if any(word in q for word in ("anomaly", "warning", "alert", "problem", "wrong", "fault")):
            alerts = [
                event for event in events
                if event.get("severity") in {"warning", "error", "critical"}
            ][-6:]
            if not alerts:
                return "No warning or critical events are recorded in the recent timeline.", ["events"]
            lines = [
                f"{event.get('receive_time', '')[-12:]} {event.get('topic')}: "
                f"{(event.get('payload') or {}).get('detail') or (event.get('payload') or {}).get('rule_id') or ''}"
                for event in alerts
            ]
            sources.extend(event.get("event_id", "event") for event in alerts)
            return "Recent warnings: " + " | ".join(lines), sources

        if any(word in q for word in ("time", "eta", "duration", "long", "finish")):
            sim = status.get("simulation") or {}
            estimate = (status.get("plan") or {}).get("estimate", {})
            elapsed = status.get("elapsed_s", 0)
            cap = self.config.get("mission", "time_cap_min", default=30.0)
            return (
                f"Mission elapsed {elapsed / 60:.1f} min against a {cap:.0f} min cap. "
                f"Plan estimate was {estimate.get('total_min_display', 'not generated')}. "
                f"Simulator time {sim.get('sim_time_s', 0) / 60:.1f} min at {sim.get('speed', 1)}×."
                if status.get("mode") == "SIMULATION"
                else f"Mission elapsed {elapsed / 60:.1f} min against a {cap:.0f} min cap.",
                ["mission.status"],
            )

        if any(word in q for word in ("link", "radio", "telemetry", "connection")):
            lines = [
                f"{vehicle['callsign']}: {vehicle.get('link')} / {vehicle.get('data_quality')}"
                for vehicle in vehicles
            ]
            sources.extend(f"fleet:{vehicle['callsign']}" for vehicle in vehicles)
            return (
                f"Telemetry topology is {fleet.get('topology')} ({fleet.get('topology_status')}). "
                + "; ".join(lines),
                sources,
            )

        if any(word in q for word in ("next", "should", "recommend", "advice", "improve")):
            recommendations = []
            if status.get("phase") == "EXECUTING":
                recommendations.append("Monitor release clearances and keep the recall control available.")
                if kits.get("total_free", 10) <= 2:
                    recommendations.append("Kit inventory is low; prepare recovery and post-mission accounting.")
            elif status.get("phase") in {"SETUP", "PLANNING"}:
                recommendations.append("Import the boundary, generate the plan, then complete physical sign-offs.")
            elif status.get("phase") in {"PREFLIGHT", "UPLOAD", "READY"}:
                recommendations.append("Resolve blocking preflight items and verify every upload receipt.")
            else:
                recommendations.append("Review the mission report and replay before the next sortie.")
            if any(vehicle.get("battery_pct") is not None and float(vehicle["battery_pct"]) < 45 for vehicle in vehicles):
                recommendations.append("At least one battery is below 45%; plan a replacement before launch.")
            sources.append("mission.status")
            return " ".join(recommendations), sources

        mode = status.get("mode", "UNKNOWN")
        phase = status.get("phase", "SETUP")
        connected = sum(1 for vehicle in vehicles if vehicle.get("connected"))
        return (
            f"Mission {status.get('mission_id') or 'not started'} is in phase {phase} ({mode}). "
            f"{connected}/{len(vehicles) or 3} aircraft connected. "
            f"{survivors.get('total', 0)} survivors, {kits.get('total_free', 0)} kits free. "
            "Ask about battery, survivors, kits, warnings, ETA, link quality or next steps.",
            ["mission.status", "fleet", "survivors", "kits"],
        )

    # ---------------------------------------------------------------- optional
    def _ollama(self, question: str, context: dict[str, Any], baseline: str) -> str | None:
        url = str(self.config.get("copilot", "ollama_url", default="http://127.0.0.1:11434"))
        model = str(self.config.get("copilot", "model", default="llama3.2"))
        prompt = (
            "You are a read-only rescue mission assistant. Answer briefly using only the JSON state. "
            "Never suggest bypassing safety rules.\n"
            f"State: {json.dumps(context, default=str)[:12000]}\n"
            f"Deterministic answer: {baseline}\nQuestion: {question}"
        )
        payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
        request = urllib.request.Request(
            f"{url.rstrip('/')}/api/generate", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                data = json.loads(response.read().decode("utf-8"))
            text = str(data.get("response", "")).strip()
            return text[:4000] or None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return None
