"""Local correction history: JSONL store for undo and before/after review.

Privacy: entries never leave the machine. Storage is append-only JSONL with
a size cap; corrupt lines are skipped on load rather than failing.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from stet.constants import APP_DATA_DIR
from stet.core.utils import log


class CorrectionHistory:
    def __init__(self, path: Optional[Path] = None, limit: int = 200, enabled: bool = True):
        self._path = Path(path) if path else APP_DATA_DIR / "history.jsonl"
        self._limit = max(1, int(limit))
        self._enabled = enabled
        self._lock = threading.Lock()

    def add(self, *, mode: str, strength: str, original: str, corrected: str,
            target_app: str = "") -> Optional[str]:
        if not self._enabled or not original or original == corrected:
            return None
        entry = {
            "id": uuid.uuid4().hex,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
            "strength": strength,
            "original": original,
            "corrected": corrected,
            "target_app": target_app,
            "undone": False,
        }
        with self._lock:
            entries = self._load_unlocked()
            entries.append(entry)
            entries = entries[-self._limit:]
            self._save_unlocked(entries)
        return entry["id"]

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._load_unlocked()))[:limit]

    def get(self, entry_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            for e in self._load_unlocked():
                if e.get("id") == entry_id:
                    return e
        return None

    def mark_undone(self, entry_id: str) -> bool:
        with self._lock:
            entries = self._load_unlocked()
            for e in entries:
                if e.get("id") == entry_id:
                    e["undone"] = True
                    self._save_unlocked(entries)
                    return True
        return False

    def remove(self, entry_id: str) -> bool:
        with self._lock:
            entries = self._load_unlocked()
            new_entries = [e for e in entries if e.get("id") != entry_id]
            if len(new_entries) == len(entries):
                return False
            self._save_unlocked(new_entries)
        return True

    def clear(self) -> None:
        with self._lock:
            self._save_unlocked([])

    def _load_unlocked(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        entries = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # skip corrupt lines
                    if isinstance(obj, dict) and "id" in obj:
                        entries.append(obj)
        except OSError as e:
            log(f"[History] load failed: {e}")
        return entries

    def _save_unlocked(self, entries: list[dict[str, Any]]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".jsonl.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            tmp.replace(self._path)
        except OSError as e:
            log(f"[History] save failed: {e}")
