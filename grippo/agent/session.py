"""Bounded conversation memory (prevents unbounded context growth)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Turn:
    user: str
    outcome: str


@dataclass
class PendingChoice:
    """A clarification waiting for one of ``choices`` to fill ``field`` of ``op``."""

    op: str
    args: dict[str, Any]
    field: str
    choices: tuple[str, ...]


class Session:
    def __init__(self, max_turns: int = 6, max_chars_per_turn: int = 400) -> None:
        self.turns: deque[Turn] = deque(maxlen=max(max_turns, 0) or 1)
        self.max_turns = max_turns
        self.max_chars_per_turn = max_chars_per_turn
        self.pending: PendingChoice | None = None

    def add(self, user: str, outcome: str) -> None:
        if self.max_turns == 0:
            return
        limit = self.max_chars_per_turn
        self.turns.append(Turn(user[:limit], outcome[:limit]))

    def render(self, max_chars: int = 2000) -> str:
        if self.max_turns == 0:
            return ""
        lines = []
        for turn in self.turns:
            lines.append(f"usuário: {turn.user}\ngrippo: {turn.outcome}")
        text = "\n".join(lines)
        return text[-max_chars:]

    def clear(self) -> None:
        self.turns.clear()
        self.pending = None
