"""End-to-end SIMULATION verification for the RescueSwarm GCS backend.

Runs the full staged workflow against the deterministic simulator:

    connect fleet -> import KML -> plan -> preflight -> upload -> gate
    -> START -> autonomous search/detect/allocate/deliver -> completion
    -> reports -> replay

Every artefact this script reports is SIMULATED. The script exits non-zero on
the first failed assertion, so it can gate a release build.

Usage (from backend/):
    python scripts/e2e_simulation.py [--speed 40] [--keep]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from rescueswarm.config import SAMPLES_DIR, data_dir  # noqa: E402


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  ok  {message}")


async def run(speed: float) -> dict:
    from rescueswarm.state import AppState

    state = AppState()
    state.config.update({"simulation": {"speed": speed, "seed": 71}})
    await state.startup()
    try:
        print("[1] fleet connect (SIMULATION)")
        connection = await state.connect_fleet("SIMULATION", operator="e2e")
        expect(connection["connection"]["mode"] == "SIMULATION", "telemetry bound to simulator")
        expect(len(connection["fleet"]["vehicles"]) == 3, "three simulated aircraft")

        print("[2] KML import and plan")
        kml = (SAMPLES_DIR / "sample_search_zone_10ha.kml").read_bytes()
        state.mission.import_kml(kml, "sample_search_zone_10ha.kml", operator="e2e")
        state.mission.create_plan(operator="e2e")
        plan = state.mission.plan
        expect(plan["search"]["lane_count"] == 9, "nine search lanes")
        expect(plan["validation"]["ok"], "plan passes geometric validation")
        expect(plan["geofences"] and len(plan["geofences"]) == 3, "three geofences")

        print("[3] manual sign-off and preflight")
        state.mission.sign_all_simulation(operator="e2e")
        before = state.mission.run_preflight(operator="e2e")
        expect(not before["ready"], "preflight blocked before upload")
        expect(any("upload" in c["check_id"] for c in before["blocking"]),
               "upload is the blocking check")

        print("[4] upload and read-back")
        upload = await state.mission.upload_mission(operator="e2e")
        expect(upload["verified"], "all three aircraft receipts VERIFIED")
        for callsign, receipt in upload["receipts"]["aircraft"].items():
            expect(receipt.get("simulated") is True, f"{callsign} receipt labelled simulated")

        print("[5] readiness gate and start")
        gate = state.mission.gate()
        if not gate["ready"]:
            raise AssertionError(f"gate blocked: {[b.get('check_id') for b in gate['blockers']]}")
        print("  ok  readiness gate open")
        state.mission.start_mission(operator="e2e", confirmation="START")
        expect(state.mission.phase == "EXECUTING", "mission executing")

        print(f"[6] autonomous mission at {speed:g}x simulation speed")
        mission_id = state.mission.mission_id
        deadline = asyncio.get_running_loop().time() + 240.0
        while state.mission.phase == "EXECUTING":
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("mission did not complete within the real-time budget")
            await asyncio.sleep(0.25)
        expect(state.mission.phase == "COMPLETE",
               f"mission completed ({state.mission.outcome})")
        summary = state.mission.completion_summary or {}
        survivors = summary["survivors"]
        kits = summary["kits"]
        counts = survivors["counts"]
        print(f"      survivors={counts} kits={kits['counts']} sim_time={summary.get('detail', {}).get('metrics', {}).get('sim_time_s')}")

        print("[7] delivery integrity")
        expectation = survivors["total"]
        expect(expectation == 10, "all ten planted survivors detected")
        expect(counts.get("UNSERVED", 0) == 0, "no survivor left UNSERVED")
        expect(counts.get("DELIVERED", 0) + counts.get("UNKNOWN", 0) == expectation,
               "every survivor resolved as DELIVERED or UNKNOWN")
        expect(kits["counts"].get("DELIVERED", 0) == counts.get("DELIVERED", 0),
               "kit ledger matches delivered count")
        expect(kits["counts"].get("UNKNOWN", 0) == counts.get("UNKNOWN", 0),
               "kit ledger matches unknown count")
        unknown = [s for s in survivors["survivors"] if s["status"] == "UNKNOWN"]
        for survivor in unknown:
            task = state.delivery.for_survivor(survivor["survivor_id"])
            expect(task is not None and task.get("beam_result") != "CONFIRMED",
                   f"{survivor['survivor_id']} UNKNOWN carries a missing-beam record")
        for assignment in state.allocator.assignments.values():
            expect(assignment["decision"]["candidates"] is not None,
                   f"{assignment['assignment_id']} decision has candidate evidence")

        print("[8] evidence artefacts")
        folder = data_dir() / "Missions" / mission_id
        for relative in ("manifest.json", "plan/plan.json", "plan/upload_receipts.json",
                         "survivors/allocations.jsonl", "reports/report.pdf",
                         "reports/report.md", "reports/report.json",
                         "reports/completion_summary.json"):
            path = folder / relative
            expect(path.exists() and path.stat().st_size > 0, f"{relative} written")
        pdf = (folder / "reports/report.pdf").read_bytes()
        expect(pdf.startswith(b"%PDF-") and b"%%EOF" in pdf[-2048:], "PDF open/close markers valid")
        for callsign in ("INS-01", "DEL-01", "DEL-02"):
            video = folder / "video" / f"{callsign}.mp4"
            expect(video.exists() and video.stat().st_size > 0, f"{callsign} recording exists")

        print("[9] replay")
        replay = state.replay.replay(mission_id)
        expect(replay["counts"]["telemetry_samples"] > 0, "telemetry track recorded")
        sample = replay["tracks"]["INS-01"][0]
        expect("agl_m" in sample and "quality" in sample, "replay samples include AGL and quality")
        expect(len(replay["timeline"]) > 0, "replay timeline populated")

        print("[10] analytics")
        analytics = state.reporter.analytics(mission_id)
        expect(analytics["survivors_registered"] == expectation, "analytics survivor total")
        return {
            "mission_id": mission_id,
            "phase": state.mission.phase,
            "counts": counts,
            "kits": kits["counts"],
            "samples": replay["counts"]["telemetry_samples"],
            "plan": {"lanes": plan["search"]["lane_count"],
                     "path_m": plan["search"]["path_length_m"]},
        }
    finally:
        await state.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--speed", type=float, default=40.0)
    parser.add_argument("--keep", action="store_true", help="keep the temporary data directory")
    args = parser.parse_args()

    temp = Path(tempfile.mkdtemp(prefix="rescueswarm-e2e-"))
    os.environ["RESCUESWARM_DATA"] = str(temp)
    print(f"data dir: {temp}")
    try:
        result = asyncio.run(run(args.speed))
        print("\nE2E PASSED")
        for key, value in result.items():
            print(f"  {key}: {value}")
        return 0
    except Exception as exc:
        print(f"\nE2E FAILED: {type(exc).__name__}: {exc}")
        return 1
    finally:
        if not args.keep:
            shutil.rmtree(temp, ignore_errors=True)
        else:
            print(f"kept: {temp}")


if __name__ == "__main__":
    raise SystemExit(main())
