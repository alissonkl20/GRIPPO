import os
import subprocess
import time
from pathlib import Path

import pytest

from grippo.engine.plan import StepStatus
from grippo.errors import RepositoryError
from grippo.git import runner
from grippo.git.repo import discover
from tests.conftest import git, make_executor, run_ops, subject, write

pytestmark = pytest.mark.git


def _hook(repo, body):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\n" + body)
    hook.chmod(0o755)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat").read_text().split()
    return stat[2] != "Z"


def test_timeout_kills_whole_process_group(repo, tmp_path):
    pidfile = tmp_path / "hook.pid"
    _hook(repo, f"echo $$ > {pidfile}\nsleep 30\n")
    executor = make_executor(repo, timeout=1.5)
    write(repo, "a.txt", "a")
    started = time.monotonic()
    result = run_ops(executor, ("add", {"paths": ["a.txt"]}), ("commit", {"message": "feat: a"}))
    assert time.monotonic() - started < 10
    assert result.status is StepStatus.TIMEOUT
    assert result.results[1].status is StepStatus.TIMEOUT
    assert "tempo limite" in result.results[1].hint
    time.sleep(0.2)
    assert not _alive(int(pidfile.read_text()))  # no orphan hook left behind
    assert subject(repo) == "chore: init"  # state reported honestly: nothing committed
    assert "a.txt" in result.final_state.staged


def test_git_killed_unexpectedly(repo):
    _hook(repo, "kill -9 $PPID\n")
    executor = make_executor(repo)
    write(repo, "a.txt", "a")
    result = run_ops(executor, ("add", {"paths": ["a.txt"]}), ("commit", {"message": "feat: a"}))
    assert result.status is StepStatus.FAILED
    assert result.results[1].returncode != 0
    assert subject(repo) == "chore: init"
    assert not result.ok


def test_interrupt_during_git(repo, monkeypatch):
    executor = make_executor(repo)
    write(repo, "a.txt", "a")
    original = subprocess.Popen.communicate
    calls = {"n": 0}

    def interrupted(self, *args, **kwargs):
        if "add" in self.args and not calls["n"]:
            calls["n"] += 1
            raise KeyboardInterrupt
        return original(self, *args, **kwargs)

    plan = executor.build_plan([("add", {"paths": ["a.txt"]})])
    prepared = executor.prepare(plan)
    from grippo.engine.permissions import Decision

    auth = executor.authorize(prepared, Decision.CONFIRM)
    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    result = executor.execute(prepared, auth)
    monkeypatch.undo()
    assert result.status is StepStatus.INTERRUPTED
    assert "interrompid" in result.message.lower()
    assert result.final_state is not None


def test_subprocess_failure_is_reported_not_hidden(repo, tmp_path):
    executor = make_executor(repo)
    assert run_ops(
        executor, ("remote_add", {"name": "origin", "url": str(tmp_path / "missing.git")})
    ).ok
    result = run_ops(executor, ("push", {"set_upstream": True}))
    assert not result.ok and result.results[0].stderr
    assert result.message == "A operação falhou."
    assert "remoto" in result.results[0].hint


def test_empty_cherry_pick_reports_intermediate_state(repo):
    result = run_ops(make_executor(repo), ("cherry_pick", {"commits": ["HEAD"]}))
    assert result.status is StepStatus.CONFLICT
    assert "sem conflitos" in result.message and "intermediário" in result.message


def test_no_live_children_after_operations(repo):
    executor = make_executor(repo)
    for i in range(10):
        write(repo, f"f{i}", "x")
        assert run_ops(executor, ("add", {"paths": [f"f{i}"]})).ok
    assert not runner._live


def test_corrupted_repository_gives_clear_error(repo):
    (repo / ".git" / "HEAD").write_text("garbage\n")
    with pytest.raises(RepositoryError):
        make_executor(repo).state()


def test_corrupted_object_detected(repo, tmp_path):
    executor = make_executor(repo)
    objects = [
        p for p in (repo / ".git" / "objects").rglob("*") if p.is_file() and len(p.parent.name) == 2
    ]
    for obj in objects:
        obj.chmod(0o644)
        obj.write_bytes(b"corrupt")
    with pytest.raises(RepositoryError, match="corrupt"):
        executor.run_read("log")


def test_outside_repository(tmp_path):
    with pytest.raises(RepositoryError, match="não está dentro"):
        discover(tmp_path)


def test_bare_repository_refused(tmp_path):
    bare = tmp_path / "b.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    with pytest.raises(RepositoryError):
        discover(bare)


def test_conflict_leaves_clear_intermediate_state(repo):
    git(repo, "switch", "-q", "-c", "other")
    write(repo, "README.md", "x\n")
    git(repo, "commit", "-qam", "docs: x")
    git(repo, "switch", "-q", "main")
    write(repo, "README.md", "y\n")
    git(repo, "commit", "-qam", "docs: y")
    result = run_ops(make_executor(repo), ("merge", {"rev": "other"}))
    assert result.status is StepStatus.CONFLICT
    assert "estado intermediário" in result.message and "README.md" in result.message


def test_outside_repository_suggests_nested_repos(tmp_path):
    from tests.conftest import init_repo

    init_repo(tmp_path / "projeto" / "app-laravel")
    (tmp_path / "projeto" / "node_modules" / "lib").mkdir(parents=True)
    init_repo(tmp_path / "projeto" / "node_modules" / "lib" / "dep")
    with pytest.raises(RepositoryError) as error:
        discover(tmp_path / "projeto")
    text = str(error.value)
    assert "app-laravel" in text and "grippo --repo app-laravel" in text
    assert "node_modules" not in text
