"""Optional local HTTP API (``pip install grippo[api]``; run with ``grippo-api``).

Security model:
* binds to 127.0.0.1 only;
* every request needs the ``X-Grippo-Token`` header (random token written with mode 0600
  to ~/.local/state/grippo/api-token) — this also defeats CSRF from web pages, which
  cannot read the token nor send custom headers without a CORS preflight;
* there is NO raw shell/git endpoint: everything goes through the same planner,
  permission manager and executor as the CLI; mutations need an explicit
  ``decision: "confirm"`` for a previously returned plan id, bound to repository state.
"""

from __future__ import annotations

import hmac
import os
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from grippo import __version__
from grippo.engine.permissions import Decision
from grippo.engine.plan import PlanResult, PreparedPlan
from grippo.errors import AuthorizationError, GrippoError
from grippo.git.repo import discover
from grippo.paths import get_paths
from grippo.runtime import Runtime, build_runtime
from grippo.skills.loader import builtin_skills, get_skill

PLAN_TTL_SEC = 300
MAX_RUNTIMES = 8
MAX_PLANS = 64


class TurnRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    cwd: str | None = None


class DecideRequest(BaseModel):
    plan_id: str
    decision: str = Field(..., pattern="^(confirm|cancel)$")


def _token_file() -> Path:
    return get_paths().state_dir / "api-token"


def ensure_token() -> str:
    path = _token_file()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(token)
    return token


def _result_payload(result: PlanResult) -> dict[str, Any]:
    return {
        "status": result.status.value,
        "message": result.message,
        "steps": [
            {
                "op": r.step.op,
                "status": r.status.value,
                "command": r.command,
                "stdout": r.stdout[-20000:],
                "stderr": r.stderr[-4000:],
                "hint": r.hint,
                "verification": r.verification.message if r.verification else None,
            }
            for r in result.results
        ],
        "state": result.final_state.to_dict() if result.final_state else None,
    }


def _preview_payload(prepared: PreparedPlan) -> dict[str, Any]:
    return {
        "risk": prepared.risk.label,
        "needs_confirmation": prepared.needs_confirmation,
        "steps": [
            {
                "op": p.step.op,
                "args": p.step.args,
                "argv": ["git", *p.argv],
                "risk": p.risk.label,
                "consequences": p.impact.consequences,
                "files": p.impact.files,
                "commits": p.impact.commits,
                "warnings": p.impact.warnings,
                "recovery": p.impact.recovery,
                "errors": p.errors,
            }
            for p in prepared.previews
        ],
        "warnings": list(prepared.plan.warnings),
        "state": prepared.state.to_dict(),
    }


def create_app(token: str, use_llm: bool = True) -> FastAPI:
    runtimes: OrderedDict[str, Runtime] = OrderedDict()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        for runtime in runtimes.values():
            runtime.close()

    app = FastAPI(
        title="GRIPPO", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan
    )
    plans: OrderedDict[str, tuple[float, str, PreparedPlan]] = OrderedDict()
    lock = threading.Lock()

    def require_token(x_grippo_token: str = Header(default="")) -> None:
        if not hmac.compare_digest(x_grippo_token, token):
            raise HTTPException(status_code=401, detail="token inválido")

    def runtime_for(cwd: str | None) -> Runtime:
        try:
            key = str(discover(Path(cwd) if cwd else Path.cwd()).root)
            with lock:
                if key in runtimes:
                    runtimes.move_to_end(key)
                    return runtimes[key]
            candidate = build_runtime(Path(key), use_llm=use_llm)
        except GrippoError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        with lock:
            if key in runtimes:  # built concurrently by another request
                candidate.close()
                return runtimes[key]
            runtimes[key] = candidate
            while len(runtimes) > MAX_RUNTIMES:
                runtimes.popitem(last=False)[1].close()
            return candidate

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/repo/state", dependencies=[Depends(require_token)])
    def repo_state(cwd: str | None = None) -> dict[str, Any]:
        return runtime_for(cwd).executor.state().to_dict()

    @app.get("/api/v1/skills", dependencies=[Depends(require_token)])
    def skills() -> list[dict[str, Any]]:
        return [
            {"id": s.id, "version": s.version, "description": s.description, "tools": list(s.tools)}
            for s in builtin_skills()
        ]

    @app.get("/api/v1/skills/{skill_id}", dependencies=[Depends(require_token)])
    def skill(skill_id: str) -> dict[str, Any]:
        found = get_skill(skill_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"skill desconhecida: {skill_id}")
        return {
            "id": found.id,
            "description": found.description,
            "tools": list(found.tools),
            "body": found.body,
        }

    @app.post("/api/v1/agent/turn", dependencies=[Depends(require_token)])
    def turn(body: TurnRequest) -> dict[str, Any]:
        runtime = runtime_for(body.cwd)
        result = runtime.assistant.handle(body.message)
        payload: dict[str, Any] = {
            "kind": result.kind,
            "message": result.message,
            "notes": result.notes,
        }
        if result.prepared is not None:
            payload["preview"] = _preview_payload(result.prepared)
        if result.result is not None:
            payload["result"] = _result_payload(result.result)
        if result.kind == "confirm" and result.prepared is not None:
            plan_id = secrets.token_urlsafe(16)
            with lock:
                now = time.monotonic()
                for key in [k for k, (t, _, _) in plans.items() if now - t > PLAN_TTL_SEC]:
                    plans.pop(key)
                plans[plan_id] = (now, str(runtime.repo.root), result.prepared)
                while len(plans) > MAX_PLANS:
                    plans.popitem(last=False)
            payload["plan_id"] = plan_id
        return payload

    @app.post("/api/v1/agent/decide", dependencies=[Depends(require_token)])
    def decide(body: DecideRequest) -> dict[str, Any]:
        with lock:
            entry = plans.pop(body.plan_id, None)
        if entry is None or time.monotonic() - entry[0] > PLAN_TTL_SEC:
            raise HTTPException(status_code=404, detail="plano inexistente ou expirado")
        _, root, prepared = entry
        runtime = runtime_for(root)
        decision = Decision.CONFIRM if body.decision == "confirm" else Decision.CANCEL
        try:
            result = runtime.assistant.decide(prepared, decision)
        except AuthorizationError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if result is None:
            return {"status": "cancelled"}
        return _result_payload(result)

    return app


def run() -> None:
    import uvicorn

    token = ensure_token()
    print(f"GRIPPO API em http://127.0.0.1:8765 — token em {_token_file()}")
    uvicorn.run(create_app(token), host="127.0.0.1", port=8765, log_level="warning")
