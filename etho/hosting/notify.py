"""Append-only operator notifications: one JSON row per line in ops/notify.jsonl.

`sent` starts false; whoever delivers the message (the lead session, via PushNotification) flips it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def notify(path: Path, source: str, kind: str, message: str, now: datetime | None = None) -> dict:
    row = {"time": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "source": source,
           "kind": kind, "message": message[:199], "sent": False}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row
