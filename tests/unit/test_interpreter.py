"""Interpreter with a scripted fake model (mock — not evidence of real LLM quality)."""

import pytest

from grippo.agent.session import Session
from grippo.engine.executor import GitExecutor
from grippo.llm.interpreter import Interpreter, sanitize_steps
from tests.conftest import FakeOllama


def _interp(fake: FakeOllama) -> Interpreter:
    return Interpreter(fake.client())


def test_llm_execute(fake_ollama: FakeOllama, executor: GitExecutor):
    fake_ollama.replies.append(
        {
            "action": "execute",
            "reply": "ok",
            "steps": [{"op": "branch_create", "args": {"name": "feat/x", "switch": True}}],
        }
    )
    result = _interp(fake_ollama).interpret(
        "preciso de um lugar pra trabalhar no x", executor.state(), Session()
    )
    assert result.source == "llm" and result.steps == [
        ("branch_create", {"name": "feat/x", "switch": True})
    ]


def test_unknown_ops_and_params_dropped():
    steps, notes = sanitize_steps(
        [
            {"op": "rm_rf", "args": {}},
            {"op": "push", "args": {"force": True, "shell": "x"}},
            "garbage",
        ]
    )
    assert steps == [("push", {"force": True})]
    assert any("rm_rf" in n for n in notes) and any("shell" in n for n in notes)


def test_precise_rule_is_authoritative_model_not_consulted(fake_ollama: FakeOllama, executor):
    """A rule marked fast (explicit syntax, e.g. a merge with a named branch) is final."""
    fake_ollama.replies.append({"action": "explode", "steps": [], "reply": ""})
    result = _interp(fake_ollama).interpret("faça merge da develop", executor.state(), Session())
    assert result.source == "router" and result.steps[0][0] == "merge"
    assert fake_ollama.calls == []


@pytest.mark.parametrize(
    ("text", "bad_reply", "expected"),
    [
        # Seen in the real CLI with qwen2.5:7b: the model invents a SHA, the grounding guard
        # then refuses — the user's exact words were enough.
        (
            "reverta o último commit",
            [{"op": "revert", "args": {"commit": "17aad37a"}}],
            ("revert", {"commit": "HEAD"}),
        ),
        (
            "exclua a branch main",
            [{"op": "branch_delete", "args": {"name": "main", "force": True}}],
            ("branch_delete", {"name": "main", "force": False}),
        ),
        (
            "faça reset --hard HEAD",
            [{"op": "reset", "args": {"mode": "hard", "target": "dda83766"}}],
            ("reset", {"mode": "hard", "target": "HEAD"}),
        ),
    ],
)
def test_precise_rules_are_not_overridden_by_a_wrong_model(
    fake_ollama: FakeOllama, executor, text, bad_reply, expected
):
    fake_ollama.replies.append({"action": "execute", "steps": bad_reply, "reply": "ok"})
    result = _interp(fake_ollama).interpret(text, executor.state(), Session())
    assert result.source == "router"
    assert result.steps[0] == expected
    assert fake_ollama.calls == []


def test_invalid_llm_answer_is_reported_when_router_has_no_rule(fake_ollama: FakeOllama, executor):
    fake_ollama.replies.append({"action": "explode", "steps": [], "reply": ""})
    result = _interp(fake_ollama).interpret(
        "faça algo estranho com o repo", executor.state(), Session()
    )
    assert result.action == "clarify"
    assert any("inválido" in n for n in result.notes)


def test_llm_down_is_reported_when_router_has_no_rule(fake_ollama: FakeOllama, executor):
    fake = FakeOllama(model="present")
    interp = Interpreter(fake.client())
    interp.llm.config.model = "absent"
    result = interp.interpret("faça algo estranho com o repo", executor.state(), Session())
    assert result.action == "clarify"
    assert interp.last_error


def test_fast_path_skips_llm(fake_ollama: FakeOllama, executor):
    interp = _interp(fake_ollama)
    interp.interpret("git status", executor.state(), Session())
    assert fake_ollama.calls == []


def test_prompt_is_bounded(fake_ollama: FakeOllama, executor):
    interp = Interpreter(fake_ollama.client(), max_prompt_chars=3000)
    session = Session(max_turns=6)
    for _ in range(50):
        session.add("x" * 1000, "y" * 1000)
    assert len(session.turns) == 6
    fake_ollama.replies.append({"action": "answer", "steps": [], "reply": "ok"})
    interp.interpret("algo ambíguo " * 100, executor.state(), session)
    user_message = fake_ollama.calls[0]["messages"][1]["content"]
    assert len(user_message) <= 3000
