"""Service 10: Event Bus (Layer 0).

An in-process pub/sub built on per-event-type ``asyncio.Queue``s. Backs trigger
run dispatch, the generated_ui approval flow, and permission approval requests.
No broker, no sockets — consistent with the zero-daemon design.

Also provides a small helper for the approval round-trip: publish an
``approval.requested`` event and await the matching ``approval.resolved`` event,
correlated by ``approval_id``.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict


class EventBus:
    """Per-event-type asyncio queues with publish/consume and an approval helper."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)

    async def publish(self, event_type: str, payload: dict) -> None:
        """Enqueue a payload on the named event type's queue."""
        await self._queues[event_type].put(payload)

    async def consume(self, event_type: str, timeout: float | None = None) -> dict:
        """Await the next payload for ``event_type``.

        Raises ``asyncio.TimeoutError`` if ``timeout`` elapses first.
        """
        queue = self._queues[event_type]
        if timeout is None:
            return await queue.get()
        return await asyncio.wait_for(queue.get(), timeout=timeout)

    async def request_approval(
        self, request: dict, timeout: float | None = None
    ) -> dict:
        """Publish an approval request and wait for its resolution.

        ``request`` must carry an ``approval_id``. Returns the resolution payload
        once an ``approval.resolved`` event with the same id arrives. Resolutions
        for other ids are re-queued so concurrent approvals don't steal each
        other's answers.
        """
        approval_id = request["approval_id"]
        await self.publish("approval.requested", request)
        while True:
            resolved = await self.consume("approval.resolved", timeout=timeout)
            if resolved.get("approval_id") == approval_id:
                return resolved
            # Not ours — put it back for the intended waiter.
            await self.publish("approval.resolved", resolved)
            await asyncio.sleep(0)  # yield so the other waiter can pick it up
