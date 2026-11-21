"""Append-only JSONL audit trail of decisions and executed Git operations."""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from grippo.logs import SESSION_ID, redact

_MAX_FIELD = 500


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        text = redact(value)
        return text if len(text) <= _MAX_FIELD else text[:_MAX_FIELD] + "…"
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


class AuditLog:
    def __init__(self, audit_dir: Path | None, max_bytes: int = 2_000_000, backups: int = 10):
        self._logger: logging.Logger | None = None
        if audit_dir is None:
            return
        audit_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        logger = logging.getLogger(f"grippo.audit.{id(self)}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = RotatingFileHandler(
            audit_dir / "audit.jsonl", maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        self._logger = logger
        self._handler = handler
        self._lock = threading.Lock()

    def record(self, event: str, **fields: Any) -> None:
        if self._logger is None:
            return
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "session": SESSION_ID,
            "event": event,
            **_clean(fields),
        }
        with self._lock:
            self._logger.info(json.dumps(entry, ensure_ascii=False, sort_keys=True))

    def close(self) -> None:
        if self._logger is not None:
            self._logger.removeHandler(self._handler)
            self._handler.close()
            self._logger = None
