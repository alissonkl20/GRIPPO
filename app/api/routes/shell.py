from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.harness.shell.runner import ShellResult, run_shell

router = APIRouter(prefix="/shell")


class ShellRunRequest(BaseModel):
    argv: list[str] = Field(..., min_length=1, description="Command argv, e.g. ['git','status']")
    cwd: str | None = None


@router.post("/run", response_model=None)
def shell_run(body: ShellRunRequest) -> ShellResult:
    try:
        repo = settings.resolve_repo_root(Path(body.cwd) if body.cwd else None)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    try:
        return run_shell(body.argv, cwd=repo, timeout_sec=settings.shell_timeout_sec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TimeoutError as e:
        raise HTTPException(status_code=408, detail=str(e)) from e


@router.get("/git")
def git_quick(
    args: str = Query(..., description="Git subcommand and flags, e.g. status --short"),
    cwd: str | None = None,
) -> ShellResult:
    argv = ["git", *args.split()]
    try:
        repo = settings.resolve_repo_root(Path(cwd) if cwd else None)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    try:
        return run_shell(argv, cwd=repo, timeout_sec=settings.shell_timeout_sec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TimeoutError as e:
        raise HTTPException(status_code=408, detail=str(e)) from e
