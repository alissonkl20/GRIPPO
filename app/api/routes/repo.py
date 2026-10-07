from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.harness.worktree.snapshot import build_snapshot

router = APIRouter(prefix="/repo")


@router.get("/snapshot")
def repo_snapshot(cwd: str | None = Query(None, description="Start search for .git from this path")):
    try:
        root = settings.resolve_repo_root(Path(cwd) if cwd else None)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    snapshot = build_snapshot(root)
    return snapshot.model_dump()
