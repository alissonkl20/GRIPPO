"""Single color palette for the CLI (amber like the cheetah, plus semantic colors).

``rich`` styles are used for everything printed through the console; ``ansi()`` builds
the few prompt strings read by prompt_toolkit. ``NO_COLOR`` disables both.
"""

from __future__ import annotations

import os
import re

from rich.theme import Theme

THEME = Theme(
    {
        "brand": "bold color(214)",
        "brand.dim": "color(172)",
        "accent": "bold cyan",
        "branch": "bold magenta",
        "sha": "color(220)",
        "path": "cyan",
        "commit": "magenta",
        "muted": "grey58",
        "ok": "bold green",
        "warn": "bold yellow",
        "err": "bold red",
        "label": "grey62",
        "key.ok": "bold green",
        "key.no": "bold red",
        "key.info": "bold cyan",
        "key.edit": "bold magenta",
        "risk.low": "bold black on green",
        "risk.moderate": "bold black on yellow",
        "risk.high": "bold white on red",
        "border.brand": "color(214)",
        "border.low": "green",
        "border.moderate": "yellow",
        "border.high": "red",
        "border.muted": "grey42",
    }
)

_CODES = {
    "brand": "1;38;5;214",
    "branch": "1;35",
    "ok": "1;32",
    "no": "1;31",
    "info": "1;36",
    "edit": "1;35",
    "warn": "1;33",
    "high": "1;37;41",
    "muted": "38;5;245",
}
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def color_enabled() -> bool:
    return not os.environ.get("NO_COLOR") and os.environ.get("TERM", "") != "dumb"


def ansi(text: str, style: str) -> str:
    if not color_enabled():
        return text
    return f"\x1b[{_CODES[style]}m{text}\x1b[0m"


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)
