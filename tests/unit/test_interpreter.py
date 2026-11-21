"""Interpreter with a scripted fake model (mock — not evidence of real LLM quality)."""

from grippo.agent.session import Session
from grippo.engine.executor import GitExecutor
from grippo.llm.interpreter import Interpreter, sanitize_steps
from tests.conftest import FakeOllama


def _interp(fake: FakeOllama) -> Interpreter:
    return Interpreter(fake.client())


def test_llm_execute(fake_ollama: FakeOllama, executor: GitExecutor):
    fake_ollama.replies.append({"action": "execute", "reply": "ok", "steps": [
        {"op": "branch_create", "args": {"name": "feat/x", "switch": True}}]})
    result = _interp(fake_ollama).interpret("preciso de um lugar pra trabalhar no x",
                                            executor.state(), Session())
    assert result.source == "llm" and result.steps == [("branch_create",
                                                         {"name": "feat/x", "switch": True})]


def test_unknown_ops_and_params_dropped():
    steps, notes = sanitize_steps([{"op": "rm_rf", "args": {}},
                                   {"op": "push", "args": {"force": True, "shell": "x"}},
                                   "garbage"])
    assert steps == [("push", {"force": True})]
    assert any("rm_rf" in n for n in notes) and any("shell" in n for n in notes)


def test_invalid_llm_answer_falls_back_to_router(fake_ollama: FakeOllama, executor):
    fake_ollama.replies.append({"action": "explode", "steps": [], "reply": ""})
    result = _interp(fake_ollama).interpret("crie uma branch feat/q", executor.state(), Session())
    assert result.source == "router" and result.steps[0][0] == "branch_create"
    assert any("inválido" in n for n in result.notes)


def test_llm_down_falls_back(fake_ollama: FakeOllama, executor):
    fake = FakeOllama(model="present")
    interp = Interpreter(fake.client())
    interp.llm.config.model = "absent"
    result = interp.interpret("crie uma branch feat/y", executor.state(), Session())
    assert result.source == "router" and result.steps[0] == ("branch_create",
                                                             {"name": "feat/y", "switch": True})
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
