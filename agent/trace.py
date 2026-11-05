"""Structured run logs: one JSON object per line (JSONL), one file per run."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path


class Trace:
    def __init__(self, log_dir: str | Path | None = "logs") -> None:
        self.run_id = uuid.uuid4().hex[:12]
        self.events: list[dict] = []
        self.path = None
        if log_dir is not None:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            self.path = Path(log_dir) / f"run_{self.run_id}.jsonl"

    def log(self, event: str, **data) -> None:
        record = {"ts": round(time.time(), 3), "run_id": self.run_id, "event": event, **data}
        self.events.append(record)
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
