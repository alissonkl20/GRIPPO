"""Runs the real `grippo` entry point in subprocesses (no LLM: --no-llm)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import commit_file, git, init_repo, subject, write

pytestmark = pytest.mark.git


def grippo(*args: str, cwd: Path, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "grippo", *args], cwd=cwd, input=stdin,
                          capture_output=True, text=True, timeout=120)


def test_version_and_help(tmp_path):
    from grippo import __version__

    assert __version__ in grippo("--version", cwd=tmp_path).stdout
    out = grippo("--help", cwd=tmp_path).stdout
    for command in ("ask", "doctor", "config", "update", "uninstall", "--repo"):
        assert command in out


def test_outside_repository_is_reported(tmp_path):
    result = grippo("status", cwd=tmp_path)
    assert result.returncode == 2 and "não está dentro de um repositório" in result.stderr


def test_subdirectory_spaces_and_repo_flag(tmp_path):
    repo = init_repo(tmp_path / "my project with spaces")
    commit_file(repo, "README.md", "x", "chore: init")
    sub = repo / "deep" / "sub dir"
    sub.mkdir(parents=True)
    result = grippo("status", cwd=sub)
    assert result.returncode == 0 and "chore: init" in result.stdout
    result = grippo("--repo", str(sub), "status", cwd=tmp_path)
    assert result.returncode == 0 and "chore: init" in result.stdout


def test_multiple_repositories_are_independent(tmp_path):
    a = init_repo(tmp_path / "a")
    b = init_repo(tmp_path / "b")
    commit_file(a, "a.txt", "a", "chore: repo a")
    commit_file(b, "b.txt", "b", "chore: repo b")
    assert "repo a" in grippo("status", cwd=a).stdout
    assert "repo b" in grippo("status", cwd=b).stdout


def test_worktree(tmp_path):
    repo = init_repo(tmp_path / "main-wt")
    commit_file(repo, "a.txt", "a", "chore: init")
    git(repo, "worktree", "add", "-q", "-b", "wt-branch", str(tmp_path / "linked"))
    result = grippo("status", cwd=tmp_path / "linked")
    assert result.returncode == 0 and "wt-branch" in result.stdout
    write(tmp_path / "linked", "w.txt", "w")
    result = grippo("--no-llm", "ask", "faça commit", cwd=tmp_path / "linked", stdin="c\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert subject(tmp_path / "linked") == "docs: add w.txt"
    assert git(repo, "log", "-1", "--format=%s", "wt-branch").stdout.strip()


def test_ask_commit_with_confirmation(repo):
    write(repo, "docs/guide.md", "# guide")
    result = grippo("--no-llm", "ask", "quero", "fazer", "um", "commit", cwd=repo, stdin="c\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "operação proposta" in result.stdout and "confirmar" in result.stdout
    assert subject(repo).startswith("docs")


def test_ask_cancel_and_eof_do_nothing(repo):
    write(repo, "a.txt", "a")
    result = grippo("--no-llm", "ask", "faça commit", cwd=repo, stdin="n\n")
    assert result.returncode == 3 and "Cancelado" in result.stdout
    result = grippo("--no-llm", "ask", "faça commit", cwd=repo, stdin="")
    assert result.returncode == 3
    assert subject(repo) == "chore: init"


def test_high_risk_needs_the_full_word(repo):
    write(repo, "README.md", "precious uncommitted work")
    result = grippo("--no-llm", "ask", "reset --hard HEAD", cwd=repo, stdin="c\ns\ny\nsim\nok\n")
    assert result.returncode == 3
    assert (repo / "README.md").read_text() == "precious uncommitted work"
    assert "RISCO ALTO" in result.stdout
    result = grippo("--no-llm", "ask", "reset --hard HEAD", cwd=repo, stdin="confirmar\n")
    assert result.returncode == 0
    assert (repo / "README.md").read_text() == "hello\n"


def test_ask_json_never_executes_mutations(repo):
    write(repo, "a.txt", "a")
    result = grippo("--no-llm", "ask", "--json", "faça commit", cwd=repo)
    payload = json.loads(result.stdout)
    assert payload["kind"] == "confirm" and payload["needs_confirmation"] is True
    assert [s["op"] for s in payload["steps"]] == ["add", "commit"]
    assert subject(repo) == "chore: init"


def test_not_a_code_agent(repo):
    result = grippo("--no-llm", "ask", "implemente a tela de login", cwd=repo)
    assert result.returncode == 0 and "só executa operações Git" in result.stdout


def test_config_and_doctor(repo, isolated_env):
    out = grippo("config", "path", cwd=repo).stdout
    assert str(isolated_env / ".config" / "grippo" / "config.toml") in out
    assert grippo("config", "init", cwd=repo).returncode == 0
    assert grippo("config", "set", "llm.model", "x:1b", cwd=repo).returncode == 0
    assert 'model = "x:1b"' in grippo("config", "show", cwd=repo).stdout
    assert grippo("config", "set", "security.autonomy_level", "9", cwd=repo).returncode == 2
    result = grippo("doctor", "--offline", "--self-test", cwd=repo)
    assert "permissão sem autorização" in result.stdout and "✘" not in result.stdout, result.stdout


def test_logs_and_audit_written_to_xdg_state(repo, isolated_env):
    write(repo, "a.txt", "a")
    grippo("--no-llm", "ask", "faça commit", cwd=repo, stdin="c\n")
    state = isolated_env / ".local" / "state" / "grippo"
    assert (state / "logs" / "grippo.log").exists()
    audit = (state / "audit" / "audit.jsonl").read_text()
    assert '"decision": "confirm"' in audit and '"op": "commit"' in audit
    assert not (repo / ".grippo").exists()  # nothing written into the managed repo
