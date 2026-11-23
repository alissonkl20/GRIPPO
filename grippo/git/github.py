"""Read-only access to GitHub pull requests through the official ``gh`` CLI.

GRIPPO never creates, merges or comments on pull requests from here: it lists them so the
user knows what is open. The argv is fixed (no shell), the call is time-limited, and a
missing or unauthenticated ``gh`` becomes a clear message instead of a crash.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from grippo.errors import GrippoError

_FIELDS = "number,title,headRefName,baseRefName,author,url,isDraft"


class GitHubError(GrippoError):
    pass


def list_pull_requests(
    root: Path, branch: str | None = None, state: str = "open", timeout: float = 20.0
) -> list[dict[str, Any]]:
    """PRs of the repository at ``root``; with ``branch``, only those whose head or base is it."""
    gh = shutil.which("gh")
    if gh is None:
        raise GitHubError(
            "GitHub CLI (gh) não encontrado. Instale-o em https://cli.github.com para eu "
            "consultar os pull requests."
        )
    if state not in {"open", "closed", "merged", "all"}:
        raise GitHubError(f"estado de PR inválido: {state!r}")
    argv = [gh, "pr", "list", "--state", state, "--limit", "100", "--json", _FIELDS]
    try:
        proc = subprocess.run(
            argv, cwd=root, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise GitHubError("o GitHub demorou demais para responder") from error
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise GitHubError(
            "gh não conseguiu listar os PRs: "
            + (detail[0] if detail else f"código {proc.returncode}")
            + ' (verifique "gh auth status")'
        )
    try:
        raw = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as error:
        raise GitHubError("resposta inesperada do gh") from error
    rows = [
        {
            "number": item.get("number"),
            "title": str(item.get("title") or ""),
            "head": str(item.get("headRefName") or ""),
            "base": str(item.get("baseRefName") or ""),
            "author": str((item.get("author") or {}).get("login") or ""),
            "url": str(item.get("url") or ""),
            "draft": bool(item.get("isDraft")),
        }
        for item in raw
        if isinstance(item, dict)
    ]
    if branch:
        rows = [r for r in rows if branch in (r["head"], r["base"])]
    return rows


def format_pull_requests(rows: list[dict[str, Any]], branch: str | None, state: str) -> str:
    where = f" em {branch}" if branch else ""
    if not rows:
        return f"Nenhum pull request {state}{where}."
    lines = [f"Pull requests {state}{where} ({len(rows)}):"]
    for row in rows:
        draft = " [rascunho]" if row["draft"] else ""
        lines.append(
            f"  #{row['number']} {row['title']}{draft}\n"
            f"      {row['head']} → {row['base']} · {row['author'] or 'autor desconhecido'}"
        )
    return "\n".join(lines)
