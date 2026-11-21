"""Shared fixtures. Every test runs in disposable directories with isolated XDG dirs and
git configuration; nothing touches the GRIPPO source repository or real user repos."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from grippo.config import LLMConfig
from grippo.engine.audit import AuditLog
from grippo.engine.executor import GitExecutor
from grippo.engine.permissions import Decision, GitPermissionManager
from grippo.engine.plan import PlanResult
from grippo.git.lock import RepoLocks
from grippo.git.repo import Repository, discover
from grippo.llm.client import OllamaClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def isolated_env(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
                 ) -> Iterator[Path]:
    home = tmp_path_factory.mktemp("home")
    gitconfig = home / ".gitconfig"
    gitconfig.write_text("[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n"
                         "[tag]\n\tgpgsign = false\n")
    for key in list(os.environ):
        if key.startswith(("GRIPPO_", "GIT_")) and not key.startswith("GRIPPO_TEST"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local/share"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local/state"))
    monkeypatch.setenv("GRIPPO_BIN_DIR", str(home / ".local/bin"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T12:00:00+0000")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T12:00:00+0000")
    monkeypatch.chdir(home)
    yield home


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Test-harness git (setup/assertions only — never the code under test)."""
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def subject(repo: Path, rev: str = "HEAD") -> str:
    return git(repo, "log", "-1", "--format=%s", rev).stdout.strip()


def write(repo: Path, rel: str, text: str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def commit_file(repo: Path, rel: str, text: str, message: str) -> str:
    write(repo, rel, text)
    git(repo, "add", "--", rel)
    git(repo, "commit", "-q", "-m", message)
    return head(repo)


def init_repo(path: Path, *, identity: bool = True) -> Path:
    assert PROJECT_ROOT not in path.resolve().parents, "test repo inside the project!"
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    if identity:
        git(path, "config", "user.name", "Test User")
        git(path, "config", "user.email", "test@example.invalid")
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = init_repo(tmp_path / "repo")
    commit_file(path, "README.md", "hello\n", "chore: init")
    return path


@pytest.fixture
def remote_repo(tmp_path: Path, repo: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo, remote


def clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True,
                   capture_output=True)
    git(dest, "config", "user.name", "Other Dev")
    git(dest, "config", "user.email", "other@example.invalid")
    return dest


def make_executor(path: Path, *, autonomy: int = 2, ttl: float = 300.0,
                  auto_approve: list[str] | None = None,
                  clock: Callable[[], float] | None = None,
                  audit_dir: Path | None = None, timeout: float = 60.0) -> GitExecutor:
    repository: Repository = discover(path)
    kwargs: dict[str, Any] = {"autonomy_level": autonomy, "ttl_sec": ttl,
                              "auto_approve": auto_approve}
    if clock is not None:
        kwargs["clock"] = clock
    return GitExecutor(
        repository,
        permissions=GitPermissionManager(**kwargs),
        audit=AuditLog(audit_dir),
        locks=RepoLocks(path.parent / f".locks-{path.name}", timeout=5),
        git_timeout=timeout,
        network_timeout=timeout,
    )


@pytest.fixture
def executor(repo: Path) -> GitExecutor:
    return make_executor(repo)


def run_ops(executor: GitExecutor, *steps: tuple[str, dict[str, Any]],
            confirm: bool = True) -> PlanResult:
    plan = executor.build_plan(steps)
    prepared = executor.prepare(plan)
    assert not prepared.errors, prepared.errors
    authorization = executor.authorize(prepared, Decision.CONFIRM) if (
        confirm and prepared.needs_confirmation) else None
    return executor.execute(prepared, authorization)


def run_op(executor: GitExecutor, op: str, **args: Any) -> PlanResult:
    return run_ops(executor, (op, args))


class FakeOllama:
    """httpx MockTransport speaking the subset of the Ollama API GRIPPO uses."""

    def __init__(self, model: str = "test-model") -> None:
        self.model = model
        self.replies: list[Any] = []  # dict → JSON content; Exception → raised; str → raw text
        self.calls: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": self.model,
                                                         "model": self.model}]})
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.0-test"})
        if path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        if path == "/api/chat":
            body = json.loads(request.content)
            self.calls.append(body)
            reply = self.replies.pop(0) if self.replies else {"action": "clarify",
                                                             "steps": [], "reply": "?"}
            if isinstance(reply, Exception):
                raise reply
            if isinstance(reply, httpx.Response):
                return reply
            content = reply if isinstance(reply, str) else json.dumps(reply)
            return httpx.Response(200, json={
                "model": self.model, "message": {"role": "assistant", "content": content},
                "eval_count": 20, "eval_duration": 200_000_000, "prompt_eval_count": 300,
                "prompt_eval_duration": 100_000_000, "load_duration": 5_000_000})
        if path == "/api/generate":
            return httpx.Response(200, json={"done": True})
        return httpx.Response(404, json={"error": "not found"})

    def client(self, **overrides: Any) -> OllamaClient:
        config = LLMConfig(model=self.model, **overrides)
        return OllamaClient(config, transport=httpx.MockTransport(self.handler))


@pytest.fixture
def fake_ollama() -> FakeOllama:
    return FakeOllama()
