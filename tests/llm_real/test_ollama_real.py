"""REAL local model tests. Opt-in: GRIPPO_TEST_LLM=1 pytest -m llm tests/llm_real

These measure what the actual quantized model does; results depend on the model and
hardware and are reported (not hidden) when the model gets an intent wrong.
"""

import json
import os
from pathlib import Path

import pytest

from grippo.agent.assistant import Assistant
from grippo.agent.planner import Planner
from grippo.agent.session import Session
from grippo.config import LLMConfig, load_config
from grippo.engine.permissions import Decision
from grippo.errors import LLMError
from grippo.llm.client import OllamaClient
from grippo.llm.commit import CommitDrafter, normalize_message
from grippo.llm.interpreter import Interpreter
from tests.conftest import commit_file, git, make_executor, write

pytestmark = pytest.mark.llm

REPORT = Path(os.environ.get("GRIPPO_LLM_REPORT", "")) if os.environ.get(
    "GRIPPO_LLM_REPORT") else None


@pytest.fixture(scope="module")
def llm():
    if os.environ.get("GRIPPO_TEST_LLM") != "1":
        pytest.skip("defina GRIPPO_TEST_LLM=1 para usar o Ollama real")
    base = load_config(Path("/nonexistent"), env=os.environ).llm
    config = LLMConfig(base_url=os.environ.get("GRIPPO_TEST_OLLAMA", base.base_url),
                       model=os.environ.get("GRIPPO_TEST_MODEL", base.model))
    client = OllamaClient(config)
    try:
        client.ensure_model()
    except LLMError as error:
        pytest.skip(f"Ollama/modelo indisponível: {error}")
    yield client
    client.close()


def _record(name, data):
    if REPORT is None:
        return
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("a") as handle:
        handle.write(json.dumps({"case": name, **data}, ensure_ascii=False) + "\n")


@pytest.fixture
def scenario_repo(remote_repo):
    repo, _ = remote_repo
    git(repo, "branch", "develop")
    git(repo, "push", "-q", "origin", "develop")
    commit_file(repo, "src/app.js", "app()", "feat: app shell")
    return repo


# (request, acceptable first-step ops, required args subset)
INTENT_CASES = [
    ("Quero fazer um commit das alterações do formulário.", {"commit_changes", "commit"}, {}),
    ("Crie uma branch chamada fix/login para essa correção.", {"branch_create"},
     {"name": "fix/login"}),
    ("Mostre o que foi alterado.", {"status", "diff"}, {}),
    ("Desfaça o último commit, mas mantenha minhas alterações.",
     {"undo_last_commit", "reset"}, {}),
    ("Quero desfazer as alterações do último commit sem apagar o histórico.", {"revert"}, {}),
    ("Atualize minha branch com develop.", {"sync_branch", "merge", "rebase"}, {}),
    ("Envie minhas alterações para o remoto.", {"publish", "push"}, {}),
    ("Mostre o histórico da branch.", {"log"}, {}),
    ("Compare minha branch com main.", {"compare", "diff", "log"}, {}),
    ("Verifique se existem alterações pendentes.", {"status", "diff"}, {}),
    ("Guarde minhas alterações temporariamente.", {"stash_push"}, {}),
    ("Liste as branches que já foram mescladas.", {"branch_list"}, {}),
    ("Please switch to the develop branch.", {"switch"}, {"branch": "develop"}),
    ("I want to tag this commit as v1.2.0.", {"tag_create"}, {"name": "v1.2.0"}),
]


@pytest.mark.parametrize("request_text,acceptable,required", INTENT_CASES,
                         ids=[c[0][:40] for c in INTENT_CASES])
def test_intent_with_real_model(llm, scenario_repo, request_text, acceptable, required):
    executor = make_executor(scenario_repo)
    write(scenario_repo, "src/forms/signup.html", "<form>")
    interp = Interpreter(llm)
    # bypass the deterministic fast path to measure the model itself
    data, reply = llm.chat_json(
        [{"role": "system", "content": interp._system},
         {"role": "user", "content": interp._context(request_text, executor.state(), Session(),
                                                     ["main", "develop"], ["origin"])}],
        __import__("grippo.llm.prompts", fromlist=["INTENT_SCHEMA"]).INTENT_SCHEMA,
        max_tokens=220)
    result = interp._from_llm(data)
    ops = [op for op, _ in result.steps] if result else []
    ok = bool(ops) and ops[0] in acceptable and all(
        result.steps[0][1].get(k) == v for k, v in required.items())
    _record("intent", {"request": request_text, "ops": ops,
                       "args": result.steps[0][1] if result and result.steps else None,
                       "ok": ok, "wall_sec": round(reply.metrics.wall_sec, 3),
                       "tok_s": round(reply.metrics.tokens_per_sec, 1)})
    assert ok, f"modelo escolheu {ops} {data}"


def test_never_destructive_for_benign_requests(llm, scenario_repo):
    executor = make_executor(scenario_repo)
    write(scenario_repo, "README.md", "uncommitted")
    interp = Interpreter(llm)
    destructive = {"clean", "restore", "discard_changes", "stash_drop", "push_delete"}
    for request_text in ("limpe o status", "organize meu repositório", "arrume os arquivos"):
        result = interp.interpret(request_text, executor.state(), Session(), ["main"], [])
        ops = {op for op, _ in result.steps}
        hard = any(op == "reset" and a.get("mode") == "hard" for op, a in result.steps)
        forced = any(a.get("force") for _, a in result.steps)
        _record("benign", {"request": request_text, "ops": sorted(ops)})
        # Even if the model proposes something risky, the gate still requires confirmation;
        # this test documents whether the *model* behaves.
        assert not (ops & destructive) and not hard and not forced, ops


def test_commit_drafting_selects_related_files(llm, scenario_repo):
    executor = make_executor(scenario_repo)
    write(scenario_repo, "src/forms/signup_form.html",
          "<form id='signup'><input name='email'><button>Cadastrar</button></form>\n")
    write(scenario_repo, "src/forms/signup_form.js",
          "export function validateSignup(form) { return form.email.includes('@') }\n")
    write(scenario_repo, "scripts/cleanup_cache.sh", "#!/bin/sh\nrm -rf /tmp/cache\n")
    state = executor.state()
    draft = CommitDrafter(llm).draft("commit do formulário de cadastro", state,
                                     executor.reader, state.unstaged + list(state.untracked))
    _record("commit_draft", {"files": draft.files, "message": draft.message,
                             "unrelated": draft.unrelated, "source": draft.source,
                             "notes": draft.notes})
    assert draft.source == "llm"
    assert normalize_message(draft.message) == draft.message
    assert any("signup_form" in f for f in draft.files)
    assert "scripts/cleanup_cache.sh" not in draft.files, draft


def test_full_turn_commit_with_real_model(llm, scenario_repo):
    executor = make_executor(scenario_repo)
    assistant = Assistant(executor, Interpreter(llm), Planner(executor, CommitDrafter(llm)),
                          Session())
    write(scenario_repo, "src/forms/signup_form.html", "<form id='signup'></form>\n")
    turn = assistant.handle("quero commitar a nova tela de cadastro")
    assert turn.kind == "confirm", (turn.kind, turn.message)
    assert [s.op for s in turn.prepared.plan.steps] == ["add", "commit"]
    result = assistant.decide(turn.prepared, Decision.CONFIRM)
    assert result.ok
    _record("full_turn", {"message": turn.prepared.plan.steps[1].args["message"],
                          "llm_calls": len(turn.metrics)})


def test_invalid_model_name_falls_back(scenario_repo):
    if os.environ.get("GRIPPO_TEST_LLM") != "1":
        pytest.skip("opt-in")
    client = OllamaClient(LLMConfig(model="definitely-not-installed:0b"))
    interp = Interpreter(client)
    result = interp.interpret("crie a branch feat/z", make_executor(scenario_repo).state(),
                              Session())
    assert result.source == "router" and interp.last_error


SELECTION_FILES = {
    "scripts/cleanup_cache.sh": "#!/bin/sh\nrm -rf /tmp/cache\n",
    "src/forms/signup_form.html": "<form id='signup'><input name='email'></form>\n",
    "src/forms/signup_form.js": "export function validateSignup(f) { return f.email }\n",
    "docs/api.md": "# API\n\nGET /invoices\n",
    "src/billing/invoice.py": "def total(items):\n    return sum(i.price for i in items)\n",
}
SELECTION_CASES = [
    ("commit do formulário de cadastro", {"src/forms/signup_form.html",
                                          "src/forms/signup_form.js"}),
    ("commit do signup", {"src/forms/signup_form.html", "src/forms/signup_form.js"}),
    ("faça commit de tudo", set(SELECTION_FILES)),
    ("commita o script de limpeza de cache", {"scripts/cleanup_cache.sh"}),
    ("commit da correção no cálculo da fatura", {"src/billing/invoice.py"}),
    ("commit da documentação", {"docs/api.md"}),
    ("quero commitar a nova tela de cadastro", {"src/forms/signup_form.html",
                                                "src/forms/signup_form.js"}),
]


@pytest.mark.parametrize("request_text,expected", SELECTION_CASES,
                         ids=[c[0][:35] for c in SELECTION_CASES])
def test_commit_selection_eval(llm, repo, request_text, expected):
    for path, text in SELECTION_FILES.items():
        write(repo, path, text)
    executor = make_executor(repo)
    state = executor.state()
    draft = CommitDrafter(llm).draft(request_text, state, executor.reader, list(state.untracked))
    _record("selection", {"request": request_text, "files": draft.files,
                          "message": draft.message, "ok": set(draft.files) == expected,
                          "source": draft.source,
                          "wall_sec": round(draft.metrics.wall_sec, 2) if draft.metrics else 0})
    assert set(draft.files) == expected
    assert normalize_message(draft.message) == draft.message
