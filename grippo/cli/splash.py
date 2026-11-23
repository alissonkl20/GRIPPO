"""Animated ASCII cat shown when the interactive mode starts.

The artwork is the black ink cat from the GRIPPO icon, converted once to Unicode
braille (2x4 dots per cell) and pasted in as static text: no image file or image
library is needed at runtime. Its golden eye blinks through four frames, and the
GRIPPO name and version are written to the right of the cat.

Performance notes: frames are built once at import time as plain strings (with ANSI
colors pre-baked); the side text is added per call because it carries the version.
Each frame is written with a single ``write``, and the animation is short (default
2 blink cycles at 8 fps ≈ 0.5 s). It is skipped entirely when the output is not a
terminal, the terminal is too small or ``TERM=dumb``, and it can be disabled with
``[ui] animation = false`` or ``GRIPPO_NO_ANIMATION=1``. Ctrl-C skips it.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable
from typing import IO

# Braille cat, one row per line; the eye cells (EYE_ROW, EYE_COLS) are overwritten per frame.
_ART = (
    "⠀⠀⠀⠀⠀⠀⠀⠀⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠘⡦⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠡⠘⣷⣤⡄⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⢐⣤⣀⣰⣿⣾⣿⣿⣿⣷⡤⡀⠀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⢀⣼⣿⣿⣿⡛⢿⢿⣿⣿⣿⣿⣶⡤⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⣾⣿⣿⣿⣿⣿⣾⣿⣿⣿⣿⣿⣿⣷⣤⡄⠀⠀⠀",
    "⠀⠀⠀⠀⠀⢻⣿⣿⣻⣿⣿⣿⣿⣿⣿⣟⣻⠿⠥⠼⡆⠀⠀⠀",
    "⠀⠀⠀⠀⢀⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣯⣄⠙⢻⣧⠂⠀⠀",
    "⠀⠀⠀⠀⢿⣹⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⣿⡿⣥⡁⠁⠀⠀",
    "⠀⠀⠀⠀⠈⠻⣿⣿⣿⣿⣿⣿⣿⣿⣾⣿⣇⠛⣷⣿⡇⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠈⠉⠉⠈⠹⣿⣿⣿⣿⣷⣇⠸⡟⠈⠢⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣊⡉⡉⢻⣿⢡⣿⠀⠋⠂⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⢀⣾⣽⣟⢛⡕⠋⠁⠀⠀⠁⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠐⣻⠿⡿⡋⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠉⠀⠈⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀",
)
EYE_ROW = 5
EYE_COLS = (7, 8)
# Eye glyph per frame: open, half, closed (lid line), half. Consecutive frames always differ.
_EYE_FRAMES = ("⣿", "⠶", "⠒", "⠶")

_GAP = "   "
ART_WIDTH = max(len(line) for line in _ART)
WIDTH = ART_WIDTH + len(_GAP) + 22  # art + gap + side text
HEIGHT = len(_ART)
FPS = 8
MIN_COLUMNS = WIDTH + 2
MIN_LINES = HEIGHT + 4

_AMBER = "\x1b[38;5;214m"  # title accent
_INK = "\x1b[38;5;252m"  # light gray ink, reads as the cat on a dark terminal
_GOLD = "\x1b[38;5;220m"  # the cat's eye
_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"
_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"
_CLEAR_EOL = "\x1b[K"


def _raw_frames() -> tuple[tuple[str, ...], ...]:
    frames = []
    for glyph in _EYE_FRAMES:
        frame = []
        for row, line in enumerate(_ART):
            if row == EYE_ROW:
                cells = list(line.ljust(ART_WIDTH))
                for col in EYE_COLS:
                    cells[col] = glyph
                line = "".join(cells)
            frame.append(line.ljust(ART_WIDTH))
        frames.append(tuple(frame))
    return tuple(frames)


def _paint(line: str, *, eye: bool) -> str:
    if not eye:
        return f"{_INK}{line}{_RESET}"
    painted = []
    for col, char in enumerate(line):
        color = _GOLD if col in EYE_COLS else _INK
        painted.append(f"{color}{char}")
    return "".join(painted) + _RESET


RAW_FRAMES = _raw_frames()
COLOR_FRAMES = tuple(
    tuple(_paint(line, eye=row == EYE_ROW) for row, line in enumerate(frame))
    for frame in RAW_FRAMES
)


def _side(version: str, color: bool) -> tuple[str, ...]:
    """Text to the right of the cat, vertically centred on it."""
    name = f"{_BOLD}{_AMBER}GRIPPO{_RESET}" if color else "GRIPPO"
    version_line = f"{_DIM}v{version}{_RESET}" if color else f"v{version}"
    tagline = f"{_DIM}assistente Git local{_RESET}" if color else "assistente Git local"
    lines = [""] * HEIGHT
    top = HEIGHT // 2 - 1
    lines[top], lines[top + 1], lines[top + 2] = name, version_line, tagline
    return tuple(lines)


def _compose(frame: tuple[str, ...], side: tuple[str, ...]) -> tuple[str, ...]:
    pairs = zip(frame, side, strict=True)
    return tuple(f"{art}{_GAP}{text}" if text else art for art, text in pairs)


def _title(version: str, color: bool) -> str:
    if not color:
        return f"  GRIPPO  v{version}  assistente Git local"
    return f"  {_BOLD}{_AMBER}GRIPPO{_RESET}  {_DIM}v{version}  assistente Git local{_RESET}"


def _join(frame: tuple[str, ...]) -> str:
    return "".join(f"{line}{_CLEAR_EOL}\n" for line in frame)


def can_animate(stream: IO[str], env: dict[str, str] | None = None) -> bool:
    env = dict(os.environ) if env is None else env
    if env.get("GRIPPO_NO_ANIMATION") or env.get("TERM", "") == "dumb":
        return False
    try:
        if not stream.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    size = shutil.get_terminal_size(fallback=(0, 0))
    return size.columns >= MIN_COLUMNS and size.lines >= MIN_LINES


def show(
    stream: IO[str],
    version: str,
    *,
    animate: bool = True,
    cycles: int = 2,
    env: dict[str, str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Draw the header. Animated only on a real, large-enough terminal."""
    env = dict(os.environ) if env is None else env
    color = not env.get("NO_COLOR") and env.get("TERM", "") != "dumb" and _isatty(stream)
    side = _side(version, color)
    frames = tuple(_compose(frame, side) for frame in (COLOR_FRAMES if color else RAW_FRAMES))
    if not (animate and cycles > 0 and can_animate(stream, env)):
        wide = shutil.get_terminal_size(fallback=(0, 0)).columns >= MIN_COLUMNS
        if _isatty(stream) and wide:
            stream.write(_join(frames[0]))
        else:
            stream.write(_title(version, color) + "\n")
        stream.flush()
        return
    rewind = f"\x1b[{HEIGHT}A\r"
    rendered = tuple(_join(frame) for frame in frames)
    delay = 1.0 / FPS
    stream.write(_HIDE_CURSOR)
    try:
        for step in range(cycles * len(rendered)):
            if step:
                stream.write(rewind)
            stream.write(rendered[step % len(rendered)])
            stream.flush()
            sleep(delay)
        stream.write(rewind + rendered[0])
    except KeyboardInterrupt:
        stream.write(rewind + rendered[0])
    finally:
        stream.write(_SHOW_CURSOR)
        stream.flush()


def _isatty(stream: IO[str]) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False
