"""Threading regressions: work started from a request worker thread must still run.

The station moved its blocking mission endpoints onto `asyncio.to_thread` so that
opening video recorders cannot delay an abort. That has a consequence nobody noticed at
first: a worker thread has no running event loop, so any service that schedules an
asyncio task from there silently fails to schedule it. These tests exercise exactly that
path, without hardware.
"""

from __future__ import annotations

import asyncio
import threading
import time

from rescueswarm.state import AppState


class StationLoop:
    """An event loop running on its own thread, for the lifetime of one test.

    The pump is a plain sleep so the loop keeps servicing callbacks while the test's own
    thread — which has no running loop — asks services to schedule work onto it.
    """

    def __init__(self, lifetime_s: float = 2.0) -> None:
        self.loop = asyncio.new_event_loop()
        self.lifetime_s = lifetime_s
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self._drive, daemon=True, name="station-loop")

    def _drive(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._pump())
        except RuntimeError:
            # The test stopped the loop early during cleanup; that is the exit we asked for.
            pass
        finally:
            self.finished.set()

    async def _pump(self) -> None:
        self.loop.call_soon(self.ready.set)
        await asyncio.sleep(self.lifetime_s)

    def __enter__(self) -> "StationLoop":
        self.thread.start()
        assert self.ready.wait(3.0), "the station event loop never started"
        return self

    def __exit__(self, *_exc: object) -> None:
        if not self.finished.is_set():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3.0)
        self.loop.close()

    def wait_for_task(self, engine) -> asyncio.Task | None:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and engine._task is None:
            time.sleep(0.05)
        return engine._task


def test_the_simulated_fleet_starts_even_when_no_loop_runs_in_the_calling_thread(
    state: AppState,
) -> None:
    """The regression that turned a simulated sortie into a still picture.

    `SimulationEngine.start` used `asyncio.create_task`, which needs a running loop in
    the calling thread. From a request worker thread it raised, the coroutine was never
    awaited, and the mission sat at EXECUTING with nothing flying it — the failure looked
    like a slow simulation, not a dead one.
    """
    with StationLoop() as station:
        state.bus.bind_loop(station.loop)
        # This thread has no running loop; that is the condition under test.
        snapshot = state.simulation.start_bench()
        assert snapshot["running"] is True
        task = station.wait_for_task(state.simulation)
        assert task is not None, "the flight loop was never scheduled on the station loop"
        assert not task.cancelled()
        state.simulation.stop()


def test_stopping_the_bench_cancels_the_task_it_scheduled(state: AppState) -> None:
    with StationLoop() as station:
        state.bus.bind_loop(station.loop)
        state.simulation.start_bench()
        task = station.wait_for_task(state.simulation)
        assert task is not None
        state.simulation.stop()
        assert state.simulation.running is False
        assert state.simulation._task is None
        # Let the loop finish its pump so the cancellation is actually processed.
        assert station.finished.wait(4.0), "the station loop did not exit"
        assert task.cancelled(), "stopping the simulation must cancel its flight task"
