"""Versioned in-process event bus with SQLite and JSONL persistence.

Fan-out is synchronous (it only touches in-memory queues); disk persistence is handed
to a worker thread. A stalled SD card or a locked SQLite file must never freeze the
mission loop or the WebSocket fan-out, so no disk write happens inside the bus lock.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections import deque
from typing import Any, Callable

from .db import Database
from .evidence import EvidenceStore
from .util import iso_now, json_safe, new_id


class EventBus:
    def __init__(self, db: Database, evidence: EvidenceStore) -> None:
        self.db = db
        self.evidence = evidence
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.RLock()
        self._sequence = 0
        self._recent: deque[dict[str, Any]] = deque(maxlen=500)
        self._mission_id: str | None = None
        self._mission_mode = "SIMULATION"
        self.on_event: Callable[[dict[str, Any]], None] | None = None
        self._persist_queue: queue.SimpleQueue | None = None
        self._persist_thread: threading.Thread | None = None
        self._persist_stop = threading.Event()
        self._persist_errors = 0
        self._written = 0

    # ------------------------------------------------------------ persistence
    def start_persistence(self) -> None:
        """Move event writes onto a worker thread (called from app startup)."""
        with self._lock:
            if self._persist_thread and self._persist_thread.is_alive():
                return
            self._persist_stop.clear()
            self._persist_queue = queue.SimpleQueue()
            self._persist_thread = threading.Thread(
                target=self._persist_worker, name="event-persistence", daemon=True
            )
            self._persist_thread.start()

    def stop_persistence(self, timeout_s: float = 2.0) -> None:
        thread = self._persist_thread
        self._persist_stop.set()
        if thread and thread.is_alive():
            thread.join(timeout=timeout_s)
        self._persist_thread = None
        self._persist_queue = None

    def _persist_worker(self) -> None:
        q = self._persist_queue
        if q is None:
            return
        while not self._persist_stop.is_set():
            try:
                item = q.get(timeout=0.1)
            except queue.Empty:
                continue
            if item.get("__flush__"):
                item["done"].set()
                continue
            self._write_event(item)

    def _write_event(self, event: dict[str, Any]) -> None:
        try:
            self.db.insert_event(event)
            category = self._category(event["topic"])
            self.evidence.append_jsonl(event["mission_id"], f"events/{category}.jsonl", event)
            self._written += 1
        except Exception:
            # Event persistence must never stop flight coordination; a failure is
            # counted so diagnostics can report that evidence is incomplete.
            self._persist_errors += 1

    def flush_pending(self, timeout_s: float = 2.0) -> bool:
        """Wait until the persistence queue drains (used by shutdown and tests)."""
        deadline = threading.Event()
        q = self._persist_queue
        if q is None:
            return True
        marker = {"__flush__": True, "done": deadline}
        q.put(marker)
        return deadline.wait(timeout_s)

    def persistence_stats(self) -> dict[str, Any]:
        return {
            "async_writer": self._persist_thread is not None and self._persist_thread.is_alive(),
            "written": self._written,
            "errors": self._persist_errors,
            "flushed": 0,
        }

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def bound_loop(self) -> asyncio.AbstractEventLoop | None:
        """The loop the station started on.

        Services that own an asyncio task need it because the request that asks them to
        start can now arrive on a worker thread, where there is no running loop at all.
        """
        return self._loop

    def set_mission(self, mission_id: str | None, mode: str = "SIMULATION") -> None:
        with self._lock:
            self._mission_id = mission_id
            self._mission_mode = mode

    @property
    def mission_id(self) -> str | None:
        return self._mission_id

    @property
    def mission_mode(self) -> str:
        return self._mission_mode

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def publish(
        self,
        topic: str,
        payload: dict[str, Any] | None = None,
        *,
        severity: str = "info",
        source: str = "GCS",
        data_quality: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            event = {
                "schema_version": 1,
                "event_id": new_id("EV"),
                "sequence": self._sequence,
                "mission_id": self._mission_id,
                "topic": topic,
                "severity": severity,
                "source": source,
                "source_time": iso_now(),
                "receive_time": iso_now(),
                "data_quality": data_quality or ("SIMULATED" if self._mission_mode == "SIMULATION" else "LIVE"),
                "payload": json_safe(payload or {}),
            }
            self._recent.append(event)
            should_persist = bool(self._mission_id) and persist
            writer = self._persist_queue if should_persist else None
            subscribers = list(self._subscribers)
        if should_persist:
            if writer is not None:
                writer.put(event)
            else:
                # No worker yet (unit tests, pre-startup calls): write inline, but
                # always outside the bus lock so a slow disk cannot block fan-out.
                self._write_event(event)
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass
        self._deliver(event, subscribers)
        return event

    def _category(self, topic: str) -> str:
        if topic.startswith("operator."):
            return "operator_actions"
        if topic.startswith("safety."):
            return "safety_events"
        if topic.startswith("command.") or topic.startswith("payload."):
            return "commands"
        if topic.startswith("video."):
            return "video_health"
        return "events"

    def _deliver(self, event: dict[str, Any], subscribers: list[asyncio.Queue]) -> None:
        if not subscribers:
            return
        loop = self._loop
        for queue in subscribers:
            if loop and loop.is_running():
                try:
                    loop.call_soon_threadsafe(self._put, queue, event)
                except RuntimeError:
                    pass
            else:
                self._put(queue, event)

    @staticmethod
    def _put(queue: asyncio.Queue, event: dict[str, Any]) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(event)
            except Exception:
                pass

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._recent)[-limit:]
