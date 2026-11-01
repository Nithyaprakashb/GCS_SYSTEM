#!/usr/bin/env python3
"""Run a full simulated sortie through the real HTTP surface and check the evidence.

This is the reproducible end-to-end check behind docs/TEST_RESULTS.md. It drives the
same API the Electron client uses (load sample boundary -> plan -> preflight ->
sign-offs -> upload -> readiness gate -> typed START -> fly to completion), then
verifies the ledger against the evidence log. Nothing here touches hardware, and no
number it prints is flight performance: every result is labelled SIMULATED.

Usage:
    python tools/sim_e2e.py                 # let the sortie complete
    python tools/sim_e2e.py --abort-after 8 # abort mid-delivery and reconcile
    python tools/sim_e2e.py --data <dir>    # use a scratch data directory
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--abort-after", type=float, default=0.0,
                        help="seconds of simulated flight before aborting (0 = fly to completion)")
    parser.add_argument("--data", default="", help="scratch data directory")
    parser.add_argument("--timeout", type=float, default=180.0, help="wall-clock timeout")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable summary")
    args = parser.parse_args()

    data_dir = Path(args.data) if args.data else Path(tempfile.mkdtemp(prefix="rs-e2e-"))
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["RESCUESWARM_DATA"] = str(data_dir)
    sys.path.insert(0, str(ROOT / "backend"))

    from fastapi.testclient import TestClient  # imported after the data dir is set
    from rescueswarm.main import app

    results: list[tuple[str, bool, str]] = []
    started = time.time()

    def step(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))
        if not args.json:
            flag = "PASS" if ok else "FAIL"
            print(f"[{flag}] {name}" + (f" — {detail}" if detail else ""))

    with TestClient(app) as client:
        mission_id = ""
        try:
            connect = client.post("/api/fleet/connect", json={"mode": "SIMULATION"})
            step("connect simulated fleet", connect.status_code == 200, str(connect.json())[:160])

            loaded = client.post("/api/mission/load-sample")
            step("load sample AOI", loaded.status_code == 200, "")
            planned = client.post("/api/mission/plan")
            step("generate plan", planned.status_code == 200, "")
            mission_id = planned.json().get("mission_id") or client.get("/api/mission/status").json().get("mission_id", "")

            preflight = client.post("/api/mission/preflight")
            step("run preflight", preflight.status_code == 200, "")
            signed = client.post("/api/mission/sign-all-sim")
            step("sign simulation manual items", signed.status_code == 200, "")
            uploaded = client.post("/api/mission/upload")
            upload_body = uploaded.json() or {}
            receipts = (upload_body.get("receipts") or {}).get("aircraft") or {}
            step("upload and read-back verify", uploaded.status_code == 200
                 and bool(receipts)
                 and all((r or {}).get("status") == "VERIFIED" for r in receipts.values()),
                 json.dumps({k: (v or {}).get("status") for k, v in receipts.items()}))

            gate = client.get("/api/mission/gate").json()
            step("readiness gate opens", gate.get("ready") is True,
                 ", ".join(str(b.get("check_id")) for b in gate.get("blockers", [])) or "ready")

            denied = client.post("/api/mission/start", json={"confirmation": ""})
            step("start without the token is refused", denied.status_code in (400, 409),
                 str(denied.json())[:120])

            started_resp = client.post("/api/mission/start", json={"confirmation": "START"})
            step("start with typed START", started_resp.status_code == 200,
                 started_resp.json().get("phase", ""))

            deadline = time.time() + args.timeout
            phase = ""
            last_progress = 0
            while time.time() < deadline:
                status = client.get("/api/mission/status").json()
                phase = status.get("phase", "")
                kits = client.get("/api/kits").json()
                counts = kits.get("counts", {})
                done = counts.get("DELIVERED", 0) + counts.get("UNKNOWN", 0)
                if done != last_progress:
                    last_progress = done
                if args.abort_after and phase == "EXECUTING" and time.time() - started > args.abort_after:
                    break
                if phase in {"COMPLETE", "ABORTED"}:
                    break
                time.sleep(0.5)

            if args.abort_after:
                aborted = client.post("/api/mission/abort", json={"reason": "e2e abort drill"})
                step("abort answers with a body (no 500)", aborted.status_code == 200,
                     f"phase={aborted.json().get('phase') if aborted.status_code == 200 else aborted.json()}")
                body = aborted.json() if aborted.status_code == 200 else {}
                step("abort reconciles kits", aborted.status_code == 200
                     and "kits_returned_to_magazine" in body and "kits_unverified" in body,
                     f"returned={len(body.get('kits_returned_to_magazine', []))} "
                     f"unverified={len(body.get('kits_unverified', []))}")
                phase = body.get("phase", phase)
                deadline = time.time() + 15
                while time.time() < deadline:
                    if client.get("/api/mission/status").json().get("phase") in {"ABORTED", "COMPLETE"}:
                        break
                    time.sleep(0.4)

            step("sortie reaches a terminal phase", phase in {"COMPLETE", "ABORTED"}, f"phase={phase}")

            kits = client.get("/api/kits").json()
            deliveries = client.get("/api/deliveries").json()
            # Read the evidence table directly: a DELIVERED badge is only worth
            # reporting if the durable payload_events row behind it exists.
            from rescueswarm.db import Database

            evidence_db = Database(data_dir / "rescueswarm.db")
            evidence = evidence_db.get_payload_events(mission_id)
            evidence_db.close()

            step("ten kits accounted for", kits.get("total") == 10,
                 f"total={kits.get('total')} counts={json.dumps(kits.get('counts', {}))}")
            terminal_ok = all(k["load_status"] in {"DELIVERED", "UNKNOWN", "LOADED", "LOST", "RETURNED"}
                              for k in kits.get("kits", []))
            step("no kit left in a half-state", terminal_ok, "")
            phantom = [
                k["kit_id"] for k in kits.get("kits", [])
                if k["final_status"] == "DELIVERED"
                and not any(e["kit_id"] == k["kit_id"] and e.get("result") == "DELIVERED" for e in evidence)
            ]
            step("every DELIVERED kit has beam evidence", not phantom, ",".join(phantom))
            unknown_not_retried = all(
                k["final_status"] != "DELIVERED" or k["load_status"] == "DELIVERED"
                for k in kits.get("kits", [])
            )
            step("UNKNOWN is never rewritten to DELIVERED", unknown_not_retried, "")
            states = [t["state"] for t in deliveries.get("tasks", [])]
            step("no delivery stuck in ACK_PENDING", not any(s == "ACK_PENDING" for s in states),
                 json.dumps(deliveries.get("counts", {})))
            step("event idempotency index held",
                 len({(e.get("mission_id"), e.get("event_id")) for e in evidence
                      if e.get("event_id")}) == len([e for e in evidence if e.get("event_id")]),
                 f"{len([e for e in evidence if e.get('event_id')])} evidence rows")

            elapsed = round(time.time() - started, 1)
            if not args.json:
                print(f"\nmode=SIMULATED data={data_dir} elapsed={elapsed}s "
                      f"delivered={kits.get('counts', {}).get('DELIVERED', 0)} "
                      f"unknown={kits.get('counts', {}).get('UNKNOWN', 0)}")
            failed = [name for name, ok, _ in results if not ok]
            if args.json:
                print(json.dumps({"mission_id": mission_id, "elapsed_s": elapsed,
                                  "counts": kits.get("counts", {}),
                                  "checks": [{"name": n, "passed": ok, "detail": d}
                                             for n, ok, d in results],
                                  "failed": failed}, indent=2))
            if failed:
                print("FAILED CHECKS: " + "; ".join(failed), file=sys.stderr)
                return 1
            return 0
        except Exception as exc:  # noqa: BLE001 - the runner must report, not trace
            step("runner completed without an exception", False, f"{type(exc).__name__}: {exc}")
            print(f"runner error: {exc}", file=sys.stderr)
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
