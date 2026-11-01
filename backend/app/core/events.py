"""Server-Sent Events (SSE) bus for live dashboard updates (spec 5.6, FR-22)."""

from __future__ import annotations

import asyncio
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        payload = {"type": event_type, "data": data or {}}
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass
            except Exception:
                pass

    def publish_threadsafe(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        payload = {"type": event_type, "data": data or {}}
        for q in list(self._subscribers):
            try:
                if self._loop and self._loop.is_running():
                    self._loop.call_soon_threadsafe(q.put_nowait, payload)
                else:
                    q.put_nowait(payload)
            except Exception:
                pass


event_bus = EventBus()
