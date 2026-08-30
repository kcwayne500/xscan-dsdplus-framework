from __future__ import annotations

import asyncio
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class Event:
    type: str
    data: dict[str, Any]
    at: str


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_running_loop()

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        event = Event(event_type, data, datetime.now(UTC).isoformat())
        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(self._deliver, event)

    def _deliver(self, event: Event) -> None:
        with self._lock:
            queues = list(self._subscribers)
        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=100)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    @staticmethod
    def serialise(event: Event) -> dict[str, Any]:
        return asdict(event)
