import subprocess
from pathlib import Path

from app.harness.agent.loop import run_turn
from app.harness.neural.router import route_intent


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "grippo@test")
    _git(repo, "config", "user.name", "Grippo")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "chore: init")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "HEAD")
    return repo, remote


def test_route_commit_push():
    assert route_intent("faça commit e push na develop") == "commit_push"
    assert route_intent("commita isso") == "commit"
    assert route_intent("manda pro remoto") == "push"


def test_commit_push_needs_confirmation(tmp_path):
    repo, _ = _init_repo(tmp_path)
    (repo / "docs.md").write_text("lexicon\n")
    result = run_turn(repo, "faça commit e push", confirm=False)
    assert result.status == "needs_confirmation"
    assert result.proposal and "docs.md" in result.proposal["files"]
    status = subprocess.run(["git", "status", "--short"], cwd=repo, capture_output=True, text=True)
    assert "docs.md" in status.stdout
    log = subprocess.run(["git", "log", "-1", "--pretty=%s"], cwd=repo, capture_output=True, text=True)
    assert log.stdout.strip() == "chore: init"


def test_commit_push_executes(tmp_path):
    repo, remote = _init_repo(tmp_path)
    (repo / "docs.md").write_text("lexicon\n")
    result = run_turn(repo, "faça commit e push", confirm=True)
    assert result.status == "done"
    assert all(s.ok for s in result.steps)
    log = subprocess.run(["git", "log", "-1", "--pretty=%s"], cwd=repo, capture_output=True, text=True)
    assert log.stdout.strip().startswith("docs:")
    remote_log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s", "refs/heads/main"],
        cwd=remote,
        capture_output=True,
        text=True,
    )
    assert remote_log.stdout.strip() == log.stdout.strip()
    assert result.snapshot.clean
