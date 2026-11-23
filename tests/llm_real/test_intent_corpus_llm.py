"""Real model against the intent corpus. Opt-in: GRIPPO_TEST_LLM=1 pytest -m llm tests/llm_real

Two sets, two measurements each:

* ``tune``     phrasings the rules were written against (optimistic by construction);
* ``holdout``  phrasings written afterwards, never used to tune anything (honest estimate).

For each phrase:

* ``pipeline``  what the user actually gets: deterministic router first, model only when no
  rule matches (Interpreter.interpret);
* ``llm_only``  the model alone, bypassing the router: the measure that tells whether
  fine-tuning would help (weak here + good pipeline = router carries the load).

Results go to GRIPPO_LLM_REPORT (JSONL) when set, and a summary is printed.
"""

import json
import os
from collections import Counter
from pathlib import Path

import pytest

from grippo.agent.router import route
from grippo.agent.session import Session
from grippo.config import LLMConfig, load_config
from grippo.errors import LLMError
from grippo.llm.client import OllamaClient
from grippo.llm.interpreter import Interpreter
from grippo.llm.prompts import INTENT_SCHEMA
from tests.conftest import commit_file, git, make_executor
from tests.llm_real.corpus import CASES, CASES_EARLIER_HOLDOUT, HOLDOUT, check_case

pytestmark = pytest.mark.llm

REPORT = Path(os.environ["GRIPPO_LLM_REPORT"]) if os.environ.get("GRIPPO_LLM_REPORT") else None
BRANCHES, REMOTES = ["main", "develop"], ["origin"]


@pytest.fixture(scope="module")
def llm():
    if os.environ.get("GRIPPO_TEST_LLM") != "1":
        pytest.skip("defina GRIPPO_TEST_LLM=1 para usar o Ollama real")
    base = load_config(Path("/nonexistent"), env=os.environ).llm
    config = LLMConfig(
        base_url=os.environ.get("GRIPPO_TEST_OLLAMA", base.base_url),
        model=os.environ.get("GRIPPO_TEST_MODEL", base.model),
    )
    client = OllamaClient(config)
    try:
        client.ensure_model()
    except LLMError as error:
        pytest.skip(f"Ollama/modelo indisponível: {error}")
    yield client
    client.close()


@pytest.fixture
def corpus_repo(remote_repo):
    repo, _ = remote_repo
    git(repo, "branch", "develop")
    git(repo, "push", "-q", "origin", "develop")
    commit_file(repo, "src/app.js", "app()", "feat: app shell")
    # Work on a feature branch, as in real use: "merge da develop na main" must switch.
    git(repo, "checkout", "-q", "-b", "feature/app")
    return repo


def _llm_only(llm, interpreter, text, state, session):
    """The model alone: no router, same prompt and schema as the pipeline."""
    messages = [
        {"role": "system", "content": interpreter._system},
        {"role": "user", "content": interpreter._context(text, state, session, BRANCHES, REMOTES)},
    ]
    try:
        data, _ = llm.chat_json(messages, INTENT_SCHEMA, max_tokens=220)
    except LLMError:  # truncated or invalid JSON is a real outcome, counted as a failure
        return "invalid", []
    parsed = Interpreter._from_llm(data)
    if parsed is None:
        return "invalid", []
    return parsed.action, parsed.steps


def _measure(llm, interpreter, state, session, label, cases):
    rows = []
    for case in cases:
        pipeline = interpreter.interpret(case["text"], state, session, BRANCHES, REMOTES)
        p_steps = [(op, dict(args)) for op, args in pipeline.steps]
        l_action, l_steps = _llm_only(llm, interpreter, case["text"], state, session)
        rows.append(
            {
                "set": label,
                "model": llm.config.model,
                "text": case["text"],
                "pipeline": {
                    "action": pipeline.action,
                    "source": pipeline.source,
                    "ops": [op for op, _ in p_steps],
                    "problems": check_case(case, pipeline.action, p_steps),
                },
                "llm_only": {
                    "action": l_action,
                    "ops": [op for op, _ in l_steps],
                    "problems": check_case(case, l_action, l_steps),
                },
                "router_answers": route(case["text"], None) is not None,
            }
        )
    if REPORT is not None:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        with REPORT.open("a") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return rows


def _summary(model, label, rows):
    total = len(rows)
    pipe = sum(1 for r in rows if not r["pipeline"]["problems"])
    alone = sum(1 for r in rows if not r["llm_only"]["problems"])
    sources = dict(Counter(r["pipeline"]["source"] for r in rows))
    print(f"\n=== {model} · {label} · {total} frases ===")
    print(f"  pipeline (o que o usuário recebe): {pipe}/{total}   fontes={sources}")
    print(f"  llm sozinho (sem roteador):        {alone}/{total}")
    for r in rows:
        if r["pipeline"]["problems"]:
            print(f"    ✗ pipeline {r['text']!r}: {r['pipeline']['problems']}")
    for r in rows:
        if r["llm_only"]["problems"]:
            print(f"    · llm-only {r['text']!r}: {r['llm_only']['problems']}")


def test_intent_corpus_with_real_model(llm, corpus_repo):
    state = make_executor(corpus_repo).state()
    interpreter = Interpreter(llm)
    tune = _measure(llm, interpreter, state, Session(), "tune", CASES)
    earlier = _measure(llm, interpreter, state, Session(), "holdout-1", CASES_EARLIER_HOLDOUT)
    holdout = _measure(llm, interpreter, state, Session(), "holdout-2", HOLDOUT)
    _summary(llm.config.model, "tune", tune)
    _summary(llm.config.model, "holdout-1 (resto do 1º holdout)", earlier)
    _summary(llm.config.model, "holdout-2 (novo, nunca ajustado)", holdout)
    # The product promise is the pipeline on phrasings it was tuned for.
    failed = [r["text"] for r in tune if r["pipeline"]["problems"]]
    assert not failed, failed
