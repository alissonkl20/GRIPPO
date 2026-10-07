import pytest

from app.harness.shell.runner import run_shell


def test_rejects_non_git_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="Only 'git'"):
        run_shell(["echo", "hi"], cwd=tmp_path)
