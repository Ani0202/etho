"""A deadline for the whole upload body, so a slow sender cannot hold an upload slot.

The proxy and uvicorn only time out an idle connection; a client that sends one byte every few seconds never idles.
For `POST <path>` this ASGI middleware gives the body `seconds` in total. Past that, the app is told the client
went away (so its form parser stops and the handler's cleanup runs, which releases the upload slot), and, when the
app has not answered yet, the sender gets a plain 408.
"""

from __future__ import annotations

import asyncio
import json
import time


class UploadDeadline:
    def __init__(self, app, path: str, seconds: float):
        self.app, self.path, self.seconds = app, path, seconds

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != self.path
                or not self.seconds or self.seconds <= 0):
            return await self.app(scope, receive, send)
        deadline = time.monotonic() + self.seconds
        expired = started = False

        async def limited():
            nonlocal expired
            if expired:
                return {"type": "http.disconnect"}
            try:
                return await asyncio.wait_for(receive(), max(0.001, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                expired = True
                return {"type": "http.disconnect"}

        async def tracked(message):
            nonlocal started
            if expired:                      # whatever the app made of the cut-off body (a 400) is replaced by the 408
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited, tracked)
        except Exception:
            if not expired:
                raise
        if expired and not started:
            body = json.dumps({"error": "upload_timeout",
                               "message": f"the upload did not finish within {self.seconds:g} s",
                               "friendly": "The upload took too long to arrive, so it was stopped. Nothing was kept "
                                           "or spent. Please try again on a faster connection, or use a shorter clip."
                               }).encode()
            await send({"type": "http.response.start", "status": 408,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode()), (b"connection", b"close")]})
            await send({"type": "http.response.body", "body": body})
