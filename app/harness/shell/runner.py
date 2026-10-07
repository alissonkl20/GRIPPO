from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel

from app.config import settings


class ShellResult(BaseModel):
    ok: bool
    argv: list[str]
    cwd: str
    returncode: int
    stdout: str
    stderr: str


def _validate_argv(argv: list[str]) -> None:
    if not argv:
        raise ValueError("argv must not be empty")
    if settings.allow_arbitrary_shell:
        return
    program = Path(argv[0]).name
    if program != "git":
        raise ValueError(
            f"Only 'git' is allowed by default (got '{program}'). "
            "Set GRIPPO_ALLOW_ARBITRARY_SHELL=1 to disable this guard."
        )


def run_shell(
    argv: list[str],
    *,
    cwd: Path,
    timeout_sec: float | None = None,
) -> ShellResult:
    _validate_argv(argv)
    timeout = timeout_sec if timeout_sec is not None else settings.shell_timeout_sec
    proc = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return ShellResult(
        ok=proc.returncode == 0,
        argv=argv,
        cwd=str(cwd.resolve()),
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
