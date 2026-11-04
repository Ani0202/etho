"""Serve the finished replay through the app, read-only, with the label on every page.

The label is added by an ASGI middleware that wraps the app (the app's own code is untouched): every HTML page the
app serves gets the banner right after `<body>`, and a small same-origin script (`/replay-tiles.js`, served here, the
page's CSP allows scripts from its own origin) that qualifies the speed and cost tiles, which the page draws in the
browser from the recorded numbers. Report pages are labelled by `ReplayBackend` when they are written.
"""

from __future__ import annotations

from .backend import banner_html

SCRIPT_PATH = "/replay-tiles.js"
SCRIPT_TAG = f'<script src="{SCRIPT_PATH}" defer></script>'

# The tiles are rebuilt by the page whenever it re-renders; a MutationObserver re-applies the note to new ones.
TILES_JS = r"""
(function () {
  var NOTES = {
    "End-to-end speed": "Replay: local compute only. Not a live measurement; the live run's speed is in the banner.",
    "Model cost": "The recorded live run's cost. This replay made no model call and spent $0."
  };
  function mark() {
    document.querySelectorAll(".tile").forEach(function (t) {
      var k = t.querySelector(".k");
      var note = k && NOTES[k.textContent.trim()];
      if (!note || t.querySelector(".replay-note")) return;
      var n = document.createElement("div");
      n.className = "s replay-note";
      n.style.fontWeight = "600";
      n.textContent = note;
      t.appendChild(n);
    });
  }
  new MutationObserver(mark).observe(document.documentElement, { childList: true, subtree: true });
  mark();
})();
"""


class LabelMiddleware:
    """Inject the replay banner into HTML responses (the home page and project pages)."""

    def __init__(self, app, live: dict | None = None):
        self.app = app
        self.live = live

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in ("GET", "HEAD"):
            return await self.app(scope, receive, send)
        if scope.get("path") == SCRIPT_PATH:
            data = TILES_JS.encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/javascript; charset=utf-8"),
                                    (b"content-length", str(len(data)).encode()), (b"cache-control", b"no-cache")]})
            return await send({"type": "http.response.body", "body": b"" if scope["method"] == "HEAD" else data})
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
                    text = text.replace(marker, marker + banner_html(self.live) + SCRIPT_TAG, 1)
                data = text.encode("utf-8")
                assert start is not None
                start["headers"] = [(k, v) for k, v in start.get("headers", [])
                                    if k.lower() != b"content-length"] + [(b"content-length", str(len(data)).encode())]
                await send(start)
                return await send({"type": "http.response.body", "body": data})
            return await send(msg)

        return await self.app(scope, receive, wrapped)
