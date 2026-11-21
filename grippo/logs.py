"""Logging setup: rotating files under ~/.local/state/grippo, redaction, session ids."""

from __future__ import annotations

import logging
import re
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

SESSION_ID = uuid.uuid4().hex[:12]

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Credentials embedded in URLs: https://user:token@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1***@"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]{20,}@"), r"\1***@"),
    (re.compile(r"\b(ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{16,}"), "***"),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}"), "***"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "***"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "***"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "***"),
    (re.compile(r"(?i)\b(authorization|bearer)\s*[:=]?\s*[A-Za-z0-9._~+/=-]{12,}"), r"\1 ***"),
    (re.compile(r"(?i)\b(password|passwd|token|secret|api[_-]?key)\s*[=:]\s*\S+"), r"\1=***"),
)


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.session = SESSION_ID
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True


_FORMAT = "%(asctime)s %(levelname)-7s [%(session)s] %(name)s: %(message)s"
_configured = False


def setup_logging(
    logs_dir: Path, level: str = "INFO", max_bytes: int = 1_000_000, backups: int = 5
) -> None:
    """Idempotent. Main log + a separate errors.log with tracebacks. Nothing goes to the TTY."""
    global _configured
    if _configured:
        return
    logs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = logging.getLogger("grippo")
    root.setLevel(level.upper())
    root.propagate = False
    context = _ContextFilter()
    formatter = logging.Formatter(_FORMAT)

    main_handler = RotatingFileHandler(
        logs_dir / "grippo.log", maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
    )
    main_handler.setFormatter(formatter)
    main_handler.addFilter(context)

    error_handler = RotatingFileHandler(
        logs_dir / "errors.log", maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    error_handler.addFilter(context)

    root.addHandler(main_handler)
    root.addHandler(error_handler)
    # httpx logs every request at INFO; keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _configured = True
