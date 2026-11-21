"""install.sh / update / uninstall in a sandbox HOME. Slow and needs PyPI access:
    pytest -m install tests/install

The update source is a temporary git copy of this checkout with synthetic tags; the real
project repository is never modified.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import PROJECT_ROOT, commit_file, git, init_repo

pytestmark = pytest.mark.install

IGNORE = shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache",
                                ".mypy_cache", "*.egg-info", "dist", "build", ".coverage")


def _version() -> str:
    text = (PROJECT_ROOT / "grippo" / "__init__.py").read_text()
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def _bump(version: str, patch: int) -> str:
    major, minor, _ = version.split(".")
    return f"{major}.{minor}.{patch}"


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    root = tmp_path_factory.mktemp("install")
    home = root / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = T\n\temail = t@example.invalid\n"
                                     "[init]\n\tdefaultBranch = main\n")
    env = {
        "HOME": str(home),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PIP_CACHE_DIR": str(Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
                             / "pip"),
        "TMPDIR": str(root),
    }
    # Source repository with tags (copy of the working tree, never the real repo).
    source = root / "source"
    shutil.copytree(PROJECT_ROOT, source, ignore=IGNORE)
    base = _version()
    run = lambda *a: subprocess.run(["git", *a], cwd=source, check=True,  # noqa: E731
                                    capture_output=True, env={**os.environ, **env})
    run("init", "-q", "-b", "main")
    run("add", "-A")
    run("commit", "-qm", f"release {base}")
    run("tag", f"v{base}")
    init = source / "grippo" / "__init__.py"
    changelog = source / "CHANGELOG.md"
    good = _bump(base, 91)
    init.write_text(init.read_text().replace(base, good))
    changelog.write_text(changelog.read_text().replace(
        "## [", f"## [{good}] - teste\n\n- versão sintética de teste\n\n## [", 1))
    run("commit", "-qam", f"release {good}")
    run("tag", f"v{good}")
    broken = _bump(base, 92)
    init.write_text(init.read_text().replace(good, broken) + "\nraise SystemExit(7)\n")
    run("commit", "-qam", f"broken {broken}")
    run("tag", f"v{broken}")
    run("reset", "-q", "--hard", f"v{base}")
    return {"root": root, "home": home, "env": env, "source": source, "base": base,
            "good": good, "broken": broken}


def sh(sandbox, *args, cwd=None, stdin="", check=True):
    result = subprocess.run(list(args), cwd=cwd or sandbox["home"], input=stdin, text=True,
                            capture_output=True, env=sandbox["env"], timeout=900)
    if check and result.returncode != 0:
        raise AssertionError(f"{args} → {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result


def grippo_bin(sandbox) -> str:
    return str(sandbox["home"] / ".local" / "bin" / "grippo")


def manifest(sandbox) -> dict:
    return json.loads((sandbox["home"] / ".local/share/grippo/install.json").read_text())


def test_01_install(sandbox):
    out = sh(sandbox, "bash", str(sandbox["source"] / "install.sh"), cwd=sandbox["source"])
    assert "instalado" in out.stdout
    assert "não está no PATH" in out.stderr  # sandbox PATH lacks ~/.local/bin: reported
    launcher = Path(grippo_bin(sandbox))
    assert launcher.is_symlink()
    assert sh(sandbox, grippo_bin(sandbox), "--version").stdout.strip().endswith(sandbox["base"])
    assert (sandbox["home"] / ".config/grippo/config.toml").exists()
    assert manifest(sandbox)["source"] == str(sandbox["source"])


def test_02_global_use_from_any_directory(sandbox):
    root = sandbox["root"]
    repos = []
    for name in ("api", "projeto com espaços"):
        repo = init_repo(root / "work" / name)
        commit_file(repo, "a.txt", name, f"chore: init {name}")
        repos.append(repo)
    for repo in repos:
        sub = repo / "deep"
        sub.mkdir(exist_ok=True)
        out = sh(sandbox, grippo_bin(sandbox), "status", cwd=sub).stdout
        assert f"chore: init {repo.name}" in out
    outside = sh(sandbox, grippo_bin(sandbox), "status", cwd=root, check=False)
    assert outside.returncode == 2 and "não está dentro" in outside.stderr
    out = sh(sandbox, grippo_bin(sandbox), "--repo", str(repos[1]), "status", cwd=root).stdout
    assert "projeto com espaços" in out
    doctor = sh(sandbox, grippo_bin(sandbox), "doctor", "--offline", cwd=repos[0], check=False)
    assert "instalação" in doctor.stdout and "via install.sh" in doctor.stdout


def test_03_reinstall_is_idempotent_and_keeps_config(sandbox):
    config = sandbox["home"] / ".config/grippo/config.toml"
    config.write_text('[llm]\nmodel = "meu-modelo:1b"\n')
    audit = sandbox["home"] / ".local/state/grippo/audit/keep.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text("{}\n")
    sh(sandbox, "bash", str(sandbox["source"] / "install.sh"), cwd=sandbox["source"])
    sh(sandbox, "bash", str(sandbox["source"] / "install.sh"), cwd=sandbox["source"])
    assert config.read_text() == '[llm]\nmodel = "meu-modelo:1b"\n'
    assert audit.exists()
    versions = list((sandbox["home"] / ".local/share/grippo/versions").iterdir())
    assert len(versions) <= 2
    assert sh(sandbox, grippo_bin(sandbox), "--version").returncode == 0


def test_04_update_check_and_update(sandbox):
    check = sh(sandbox, grippo_bin(sandbox), "update", "--check")
    assert f"nova versão: {sandbox['broken']}" in check.stdout  # newest stable tag
    assert sh(sandbox, grippo_bin(sandbox), "--version").stdout.strip().endswith(sandbox["base"])
    # the broken tag is the newest, so ask explicitly for the good one first
    out = sh(sandbox, grippo_bin(sandbox), "update", "--version", sandbox["good"], "--yes")
    assert "validado" in out.stdout and "versão sintética de teste" in out.stdout
    assert sh(sandbox, grippo_bin(sandbox), "--version").stdout.strip().endswith(sandbox["good"])
    assert manifest(sandbox)["version"] == sandbox["good"]
    assert 'meu-modelo:1b' in (sandbox["home"] / ".config/grippo/config.toml").read_text()


def test_05_failed_update_rolls_back(sandbox):
    result = sh(sandbox, grippo_bin(sandbox), "update", "--yes", check=False)
    assert result.returncode == 1
    assert "rollback" in result.stderr
    assert sh(sandbox, grippo_bin(sandbox), "--version").stdout.strip().endswith(sandbox["good"])


def test_06_update_requires_explicit_consent(sandbox):
    result = sh(sandbox, grippo_bin(sandbox), "update", "--version", sandbox["base"], stdin="n\n",
                check=False)
    assert result.returncode == 3 and "cancelada" in result.stdout
    assert sh(sandbox, grippo_bin(sandbox), "--version").stdout.strip().endswith(sandbox["good"])
    out = sh(sandbox, grippo_bin(sandbox), "update", "--version", sandbox["base"], "--yes")
    assert "DOWNGRADE" in out.stdout
    assert sh(sandbox, grippo_bin(sandbox), "--version").stdout.strip().endswith(sandbox["base"])


def test_07_uninstall_preserves_user_data_and_reinstall(sandbox):
    home = sandbox["home"]
    cancel = sh(sandbox, grippo_bin(sandbox), "uninstall", stdin="n\n", check=False)
    assert cancel.returncode == 3 and Path(grippo_bin(sandbox)).exists()
    sh(sandbox, grippo_bin(sandbox), "uninstall", "--yes")
    assert not Path(grippo_bin(sandbox)).exists()
    assert not (home / ".local/share/grippo/versions").exists()
    assert (home / ".config/grippo/config.toml").exists()
    assert (home / ".local/state/grippo/audit/keep.jsonl").exists()
    assert (home / ".local/share/grippo/models").exists()
    sh(sandbox, "bash", str(sandbox["source"] / "install.sh"), "--clean", cwd=sandbox["source"])
    assert 'meu-modelo:1b' in (home / ".config/grippo/config.toml").read_text()


def test_08_purge_requires_phrase(sandbox):
    home = sandbox["home"]
    wrong = sh(sandbox, grippo_bin(sandbox), "uninstall", "--purge", stdin="sim\n", check=False)
    assert wrong.returncode == 3 and (home / ".config/grippo").exists()
    sh(sandbox, grippo_bin(sandbox), "uninstall", "--purge", stdin="remover tudo\n")
    for path in (home / ".config/grippo", home / ".local/share/grippo",
                 home / ".local/state/grippo", home / ".local/bin/grippo"):
        assert not path.exists(), path


def test_09_never_touched_project_repo():
    status = git(PROJECT_ROOT, "tag", "--list").stdout
    assert "v0.2.91" not in status and "v0.2.92" not in status


def test_10_login_shell_finds_grippo_after_add_to_path(sandbox):
    """Simulates a new terminal / reboot: only ~/.profile is read."""
    sh(sandbox, "bash", str(sandbox["source"] / "install.sh"), "--add-to-path",
       cwd=sandbox["source"])
    profile = (sandbox["home"] / ".profile").read_text()
    sh(sandbox, "bash", str(sandbox["source"] / "install.sh"), "--add-to-path",
       cwd=sandbox["source"])
    assert (sandbox["home"] / ".profile").read_text() == profile  # idempotent
    login = subprocess.run(["env", "-i", f"HOME={sandbox['home']}", "PATH=/usr/bin:/bin",
                            "bash", "-l", "-c", "command -v grippo && grippo --version"],
                           capture_output=True, text=True, timeout=60)
    assert login.returncode == 0, login.stderr
    assert str(sandbox["home"] / ".local/bin/grippo") in login.stdout
    sh(sandbox, grippo_bin(sandbox), "uninstall", "--purge", "--yes")
