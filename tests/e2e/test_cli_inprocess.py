"""In-process CLI tests (so coverage sees grippo/cli and grippo/manage)."""

import io
import json
import sys

import pytest

from grippo.cli.confirm import Choice, parse_choice
from grippo.cli.main import main
from grippo.engine.operations import CATALOG, Risk
from grippo.skills.loader import builtin_skills, get_skill
from tests.conftest import subject, write

pytestmark = pytest.mark.git


def run(monkeypatch, capsys, *argv, stdin=""):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_status_and_ask_commit(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    code, out, _ = run(monkeypatch, capsys, "status")
    assert code == 0 and "chore: init" in out
    write(repo, "docs/a.md", "# a")
    code, out, _ = run(monkeypatch, capsys, "--no-llm", "ask", "faça", "commit",
                       stdin="x\nd\ne\ndocs: write the a page\nc\n")
    assert code == 0, out
    assert "Resposta ambígua" in out and "comando:" in out
    assert subject(repo) == "docs: write the a page"


def test_ask_cancel_after_invalid_answers(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    write(repo, "a.txt", "a")
    code, out, _ = run(monkeypatch, capsys, "--no-llm", "ask", "faça commit",
                       stdin="talvez\nquem sabe\n???\nhmm\nok?\n")
    assert code == 3 and "cancelada" in out
    assert subject(repo) == "chore: init"


def test_ask_read_only_and_errors(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    code, out, _ = run(monkeypatch, capsys, "--no-llm", "ask", "mostre o histórico")
    assert code == 0 and "chore: init" in out
    code, out, _ = run(monkeypatch, capsys, "--no-llm", "ask", "mude para a branch inexistente")
    assert code == 1 and "inexistente" in out
    code, out, _ = run(monkeypatch, capsys, "--no-llm", "ask", "--json", "qual o clima?")
    assert json.loads(out)["kind"] == "clarify"


def test_outside_repo_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code, _, err = run(monkeypatch, capsys, "status")
    assert code == 2 and "não está dentro" in err


def test_config_commands(monkeypatch, capsys, isolated_env):
    code, out, _ = run(monkeypatch, capsys, "config", "path")
    assert code == 0 and "audit" in out
    assert run(monkeypatch, capsys, "config", "init")[0] == 0
    assert run(monkeypatch, capsys, "config", "set", "git.timeout_sec", "30")[0] == 0
    code, out, _ = run(monkeypatch, capsys, "config", "show")
    assert "timeout_sec = 30.0" in out
    code, _, err = run(monkeypatch, capsys, "config", "set", "nope.x", "1")
    assert code == 2 and "seção desconhecida" in err


def test_doctor_json_offline(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    code, out, _ = run(monkeypatch, capsys, "doctor", "--offline", "--json", "--self-test")
    checks = {c["name"]: c["status"] for c in json.loads(out)}
    assert checks["git"] == "OK" and checks["repositório"] == "OK"
    assert checks["permissão sem autorização"] == "OK" and checks["upload-pack"] == "OK"
    assert code == 0


def test_update_and_uninstall_without_install(monkeypatch, capsys):
    code, _, err = run(monkeypatch, capsys, "update", "--check")
    assert code == 2 and "install.sh" in err
    code, out, _ = run(monkeypatch, capsys, "uninstall", "--yes")
    assert code == 0 and "nenhuma instalação" in out


def test_parse_choice_rules():
    assert parse_choice("c", Risk.MODERATE, False) is Choice.CONFIRM
    assert parse_choice("c", Risk.HIGH, False) is Choice.INVALID
    assert parse_choice("sim", Risk.HIGH, False) is Choice.INVALID
    assert parse_choice("confirmar", Risk.HIGH, False) is Choice.CONFIRM
    assert parse_choice("", Risk.MODERATE, False) is Choice.INVALID
    assert parse_choice("e", Risk.MODERATE, False) is Choice.INVALID
    assert parse_choice("e", Risk.MODERATE, True) is Choice.EDIT
    assert parse_choice("N", Risk.HIGH, True) is Choice.CANCEL


def test_builtin_skills_reference_real_operations():
    skills = builtin_skills()
    assert {s.id for s in skills} >= {"inspect-worktree", "stage-changes", "semantic-commit",
                                      "push", "stash-save", "stash-restore", "branch-create",
                                      "branch-switch"}
    for skill in skills:
        assert set(skill.tools) <= set(CATALOG), (skill.id, skill.tools)
    assert get_skill("push") is not None and get_skill("nope") is None


@pytest.mark.parametrize("word", ["exit", "EXIT", "sair", "quit", "/exit", "/sair", "q", "sair."])
def test_exit_words(word):
    from grippo.cli.interface import is_exit

    assert is_exit(word)


@pytest.mark.parametrize("text", ["exit code do script", "sair da branch develop", "commit"])
def test_not_exit(text):
    from grippo.cli.interface import is_exit

    assert not is_exit(text)


def test_repl_exits_on_word_and_double_ctrl_c(repo, monkeypatch):
    from grippo.cli import interface
    from grippo.runtime import build_runtime

    runtime = build_runtime(repo, use_llm=False)
    monkeypatch.setattr(interface.renderer, "show_header", lambda **_: None)
    for script in (["mostre o status", "exit"], [KeyboardInterrupt, KeyboardInterrupt]):
        items = iter(script)

        def fake_reader(_history, items=items):
            def read(_prompt):
                item = next(items)
                if item is KeyboardInterrupt:
                    raise KeyboardInterrupt
                return item
            return read

        monkeypatch.setattr(interface, "make_reader", fake_reader)
        assert interface.repl(runtime) == 0
    runtime.close()
