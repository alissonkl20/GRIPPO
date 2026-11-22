"""Proposals: how an external AI *asks* for a mutating operation without being able to run it.

The AI (over MCP) can only create a proposal. A human approves it in a terminal with
``grippo approve <id>``; that process — which the AI client cannot drive — shows the real
impact, collects the confirmation and executes. The store is a directory of small JSON
files (0600), one per proposal, so both processes share state without a daemon.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class Status(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass
class Proposal:
    id: str
    repo: str
    steps: list[dict[str, Any]]
    user_request: str
    reason: str
    risk: str
    binding: str
    created: float
    expires: float
    status: str = Status.PENDING
    result: str = ""
    client: str = ""
    history: list[str] = field(default_factory=list)

    @property
    def pending(self) -> bool:
        return self.status == Status.PENDING


class ProposalStore:
    def __init__(self, directory: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.directory = directory
        self._clock = clock
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, proposal_id: str) -> Path:
        safe = "".join(ch for ch in proposal_id if ch.isalnum() or ch == "-")
        return self.directory / f"{safe}.json"

    def create(
        self,
        *,
        repo: str,
        steps: list[dict[str, Any]],
        user_request: str,
        reason: str,
        risk: str,
        binding: str,
        ttl_sec: float,
        client: str = "",
    ) -> Proposal:
        now = self._clock()
        proposal = Proposal(
            id="p-" + secrets.token_hex(4),
            repo=repo,
            steps=steps,
            user_request=user_request,
            reason=reason,
            risk=risk,
            binding=binding,
            created=now,
            expires=now + ttl_sec,
            client=client,
        )
        self.save(proposal)
        return proposal

    def save(self, proposal: Proposal) -> None:
        path = self._path(proposal.id)
        temporary = path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(proposal), handle, ensure_ascii=False, indent=2)
        os.replace(temporary, path)

    def get(self, proposal_id: str) -> Proposal | None:
        try:
            data = json.loads(self._path(proposal_id).read_text(encoding="utf-8"))
            proposal = Proposal(**data)
        except (OSError, ValueError, TypeError):
            return None
        if proposal.pending and self._clock() > proposal.expires:
            proposal.status = Status.EXPIRED
            self.save(proposal)
        return proposal

    def list(self, *, repo: str | None = None, only_pending: bool = False) -> list[Proposal]:
        found = (self.get(path.stem) for path in self.directory.glob("p-*.json"))
        return sorted(
            (
                p
                for p in found
                if p and (not repo or p.repo == repo) and (p.pending or not only_pending)
            ),
            key=lambda p: p.created,
        )

    def finish(self, proposal: Proposal, status: Status, result: str) -> None:
        proposal.status = Status(status)  # rejects anything that is not a known status
        proposal.result = result
        self.save(proposal)
