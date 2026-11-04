"""Serve the finished replay through the app, read-only, with the label on every page.

The label is added by an ASGI middleware that wraps the app (the app's own code is untouched): every HTML page the
app serves gets the banner right after `<body>`. Report pages are labelled by `ReplayBackend` when they are written.
"""

from __future__ import annotations

from .backend import banner_html


class LabelMiddleware:
    """Inject the replay banner into HTML responses (the home page and project pages)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in ("GET", "HEAD"):
            return await self.app(scope, receive, send)
        start: dict | None = None
        body = bytearray()
        html_page = False

        async def wrapped(msg):
            nonlocal start, html_page
            if msg["type"] == "http.response.start":
                headers = {k.lower(): v for k, v in msg.get("headers", [])}
                html_page = msg.get("status") == 200 and headers.get(b"content-type", b"").startswith(b"text/html")
                if not html_page:
                    return await send(msg)
                start = msg
                return None
            if msg["type"] == "http.response.body" and html_page:
                body.extend(msg.get("body", b""))
                if msg.get("more_body"):
                    return None
                text = body.decode("utf-8", "replace")
                marker = "<body>"
                if marker in text and "replay-label" not in text:     # a report page carries its own label
                    text = text.replace(marker, marker + banner_html(), 1)
                data = text.encode("utf-8")
                assert start is not None
                start["headers"] = [(k, v) for k, v in start.get("headers", [])
                                    if k.lower() != b"content-length"] + [(b"content-length", str(len(data)).encode())]
                await send(start)
                return await send({"type": "http.response.body", "body": data})
            return await send(msg)

        return await self.app(scope, receive, wrapped)
