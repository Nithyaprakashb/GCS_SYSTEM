"""Versioned in-process event bus with SQLite and JSONL persistence."""

from __future__ import annotations

import asyncio
import json
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

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

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
            if self._mission_id and persist:
                try:
                    self.db.insert_event(event)
                    category = self._category(topic)
                    self.evidence.append_jsonl(self._mission_id, f"events/{category}.jsonl", event)
                except Exception:
                    # Event persistence must never stop flight coordination.
                    pass
            subscribers = list(self._subscribers)
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
