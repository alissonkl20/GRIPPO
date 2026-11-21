"""Animated ASCII cheetah shown when the interactive mode starts.

Performance notes: frames are built once at import time as plain strings (with ANSI
colors pre-baked), written with a single ``write`` per frame, and the animation is
short (default 2 gait cycles at 14 fps ≈ 0.6 s). It is skipped entirely when the
output is not a terminal, the terminal is too small or ``TERM=dumb``, and it can be
disabled with ``[ui] animation = false`` or ``GRIPPO_NO_ANIMATION=1``. Ctrl-C skips it.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable
from typing import IO

_BODY = (
    r"                                 ,_      ",
    r"          _.--~~~~~~~~~~~~~~--._/ o`\_   ",
    r"  {tail}' ·: .: ·: .: ·: .: ·:     .__>  ",
    r"        (  .: ·: .: ·: .: ·:  _.--'      ",
    r"         `~--.__________.--~~'           ",
)
_TAILS = ("~~-._.-", "_.-~-._", "-._.-~~", "._.-~-.")
_LEGS = (
    (r"         _//                 \\_         ", r"        '                       `        "),
    (r"           //               \\          ", r"          ``                  ``         "),
    (r"              \\         //             ", r"               ``       ''              "),
    (r"             ||           ||            ", r"             ''           ''            "),
)
_GROUND = "·   .    ·  .     ·    .   ·  .    ·    "

WIDTH = max(
    len(line)
    for line in (
        *(b.replace("{tail}", _TAILS[0]) for b in _BODY),
        *(leg for pair in _LEGS for leg in pair),
        _GROUND,
    )
)
HEIGHT = len(_BODY) + 2 + 1
FPS = 14
MIN_COLUMNS = WIDTH + 2
MIN_LINES = HEIGHT + 6

_FUR = "\x1b[38;5;214m"  # amber
_SPOT = "\x1b[38;5;94m"  # dark brown
_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"
_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"
_CLEAR_EOL = "\x1b[K"


def _raw_frames() -> tuple[tuple[str, ...], ...]:
    frames = []
    for index, (upper, lower) in enumerate(_LEGS):
        body = tuple(line.replace("{tail}", _TAILS[index]).ljust(WIDTH) for line in _BODY)
        shift = index * 3
        ground = (_GROUND[shift:] + _GROUND[:shift]).ljust(WIDTH)
        frames.append((*body, upper.ljust(WIDTH), lower.ljust(WIDTH), ground))
    return tuple(frames)


def _paint(line: str, *, ground: bool) -> str:
    if ground:
        return f"{_DIM}{line}{_RESET}"
    painted = []
    for char in line:
        painted.append(f"{_SPOT}{char}{_FUR}" if char in "·:" else char)
    return f"{_FUR}{''.join(painted)}{_RESET}"


RAW_FRAMES = _raw_frames()
COLOR_FRAMES = tuple(
    tuple(_paint(line, ground=row == HEIGHT - 1) for row, line in enumerate(frame))
    for frame in RAW_FRAMES
)


def _title(version: str, color: bool) -> str:
    if not color:
        return f"  GRIPPO  v{version}  assistente Git local"
    return f"  {_BOLD}{_FUR}GRIPPO{_RESET}  {_DIM}v{version}  assistente Git local{_RESET}"


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
    frames = COLOR_FRAMES if color else RAW_FRAMES
    if not (animate and cycles > 0 and can_animate(stream, env)):
        wide = shutil.get_terminal_size(fallback=(0, 0)).columns >= MIN_COLUMNS
        if _isatty(stream) and wide:
            stream.write(_join(frames[0]))
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
        stream.write(_SHOW_CURSOR + _title(version, color) + "\n")
        stream.flush()


def _isatty(stream: IO[str]) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False
