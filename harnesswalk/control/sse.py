"""Event streams (SSE bodies) that end when the server stops.

The web app holds `GET /v1/events` open for as long as a tab is, and `GET /v1/turns/{id}/events`
for a whole turn. Uvicorn's shutdown waits for every connection to close, so a stream that never
ends would keep Ctrl+C from stopping the server. Every SSE body goes through `until_stopped`:
when `Harness.stopping` is set (`Harness.request_stop`, called from the server's signal
handler) the source is cancelled where it waits, its `finally` blocks run (wakers and live
queues removed, stream slots given back), and the response completes normally.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator


async def until_stopped[T](
    source: AsyncIterator[T], stopping: asyncio.Event | None
) -> AsyncIterator[T]:
    """The items of `source` until it ends or `stopping` is set, whichever comes first.

    `source` is closed however this ends (stopped, exhausted, or the consumer went away). With
    no event (a harness that was never started) it is `source` unchanged.
    """
    if stopping is None:
        async for item in source:
            yield item
        return
    stop = asyncio.ensure_future(stopping.wait())
    step: asyncio.Future[T] | None = None
    try:
        while not stopping.is_set():
            step = asyncio.ensure_future(anext(source))
            await asyncio.wait({step, stop}, return_when=asyncio.FIRST_COMPLETED)
            if not step.done():
                return  # stopped while the source waited: the finally cancels it
            try:
                item = step.result()
            except StopAsyncIteration:
                return
            step = None
            yield item
    finally:
        stop.cancel()
        if step is not None and not step.done():
            step.cancel()  # the source sees CancelledError where it waits, and runs its finally
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await step
        aclose = getattr(source, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()
