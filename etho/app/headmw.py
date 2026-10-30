"""HEAD support for every GET route with one ASGI middleware.

HEAD is routed as GET on a copy of the scope (uvicorn keeps its own scope, so it still knows the request was a HEAD
and sends no body). The app runs as for GET, so auth, guards, 404s and headers are identical. The body is never
produced: once the response headers have been sent we stop the app by raising, so a 17 MB clip is not read.
Event streams are the exception, because aborting them would skip the handler's own cleanup: the receive channel
reports an immediate disconnect and the body is dropped, so a stream ends after its first message.
"""

from __future__ import annotations


class _Done(Exception):
    """Raised inside send once the headers of a HEAD response are out."""


class HeadMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "HEAD":
            return await self.app(scope, receive, send)
        stream = False
        started = False

        async def head_send(message):
            nonlocal stream, started
            if message["type"] == "http.response.start":
                started = True
                headers = {k.lower(): v for k, v in message.get("headers", [])}
                stream = headers.get(b"content-type", b"").startswith(b"text/event-stream")
                await send(message)
                return
            if message["type"] == "http.response.body":
                if stream:
                    return                          # drop it; the handler ends on the disconnect below
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                raise _Done
            # other messages (pathsend, push) are not needed for a body-less reply

        async def head_receive():
            return {"type": "http.disconnect"}

        try:
            await self.app({**scope, "method": "GET"}, head_receive, head_send)
        except _Done:
            return
        if stream and started:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
