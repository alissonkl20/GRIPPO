from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.harness.agent.loop import AgentResult, run_turn

router = APIRouter(prefix="/agent")


class AgentRunRequest(BaseModel):
    message: str = Field(..., min_length=1)
    confirm: bool = False
    cwd: str | None = None


class AgentStepOut(BaseModel):
    tool: str
    ok: bool
    argv: list[str]
    stdout: str
    stderr: str


class AgentRunResponse(BaseModel):
    status: str
    intent: str
    message: str
    proposal: dict | None
    steps: list[AgentStepOut]
    snapshot: dict


def _to_response(result: AgentResult) -> AgentRunResponse:
    return AgentRunResponse(
        status=result.status,
        intent=result.intent,
        message=result.message,
        proposal=result.proposal,
        steps=[
            AgentStepOut(
                tool=s.tool,
                ok=s.ok,
                argv=s.argv,
                stdout=s.stdout,
                stderr=s.stderr,
            )
            for s in result.steps
        ],
        snapshot=result.snapshot.model_dump(),
    )


@router.post("/run", response_model=AgentRunResponse)
def agent_run(body: AgentRunRequest) -> AgentRunResponse:
    try:
        repo = settings.resolve_repo_root(Path(body.cwd) if body.cwd else None)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    try:
        result = run_turn(repo, body.message, confirm=body.confirm)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _to_response(result)
