"""The five reference scenarios driven through the Assistant.

`router` variants use no LLM. `scripted` variants use a fake model that returns a fixed
JSON (mock): they validate the pipeline, not the model's intelligence (see tests/llm_real).
"""

import pytest

from grippo.agent.assistant import Assistant
from grippo.agent.planner import Planner
from grippo.agent.session import Session
from grippo.engine.permissions import Decision
from grippo.llm.commit import CommitDrafter
from grippo.llm.interpreter import Interpreter
from tests.conftest import FakeOllama, clone, commit_file, git, head, make_executor, subject, write

pytestmark = pytest.mark.git


def assistant_for(repo, llm=None):
    executor = make_executor(repo)
    return Assistant(executor, Interpreter(llm), Planner(executor, CommitDrafter(llm)), Session())


def confirm(assistant, turn):
    assert turn.kind == "confirm", (turn.kind, turn.message)
    return assistant.decide(turn.prepared, Decision.CONFIRM)


def test_scenario1_commit_form_changes(repo):
    commit_file(repo, "src/app.js", "app", "feat: app")
    write(repo, "src/forms/signup_form.html", "<form></form>")
    write(repo, "src/forms/signup_form.js", "validate()")
    write(repo, "src/app.js", "app + form route")
    write(repo, "scripts/benchmark.py", "unrelated")
    assistant = assistant_for(repo)
    turn = assistant.handle("Quero fazer um commit das alterações do formulário (form).")
    steps = turn.prepared.plan.steps
    staged = steps[0].args["paths"]
    assert "scripts/benchmark.py" not in staged
    assert any("signup_form" in p for p in staged)
    assert any("benchmark" in w for w in turn.prepared.plan.warnings)
    assert turn.prepared.needs_confirmation
    assert head(repo) == git(repo, "rev-parse", "HEAD").stdout.strip()  # nothing done yet
    result = confirm(assistant, turn)
    assert result.ok and (repo / "scripts/benchmark.py").exists()
    assert "scripts/benchmark.py" in result.final_state.untracked


def test_scenario2_undo_last_commit_keep_changes(repo):
    commit_file(repo, "work.txt", "w", "feat: work")
    assistant = assistant_for(repo)
    turn = assistant.handle("Desfaça o último commit, mas mantenha minhas alterações.")
    step = turn.prepared.plan.steps[0]
    assert (step.op, step.args["mode"], step.args["target"]) == ("reset", "soft", "HEAD~1")
    assert any("staged" in c for c in turn.prepared.previews[0].impact.consequences)
    result = confirm(assistant, turn)
    assert result.ok and subject(repo) == "chore: init" and "work.txt" in result.final_state.staged


def test_scenario3_revert_without_rewriting(repo):
    sha = commit_file(repo, "bad.txt", "bad", "feat: bad change")
    assistant = assistant_for(repo)
    turn = assistant.handle(f"Quero desfazer as alterações do commit {sha[:8]} sem apagar o "
                            "histórico.")
    assert turn.prepared.plan.steps[0].op == "revert"
    assert "NOVO commit" in turn.prepared.previews[0].impact.consequences[0]
    result = confirm(assistant, turn)
    assert result.ok and not (repo / "bad.txt").exists()
    assert git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "3"


def test_scenario4_update_branch_asks_merge_or_rebase(remote_repo, tmp_path):
    repo, remote = remote_repo
    git(repo, "switch", "-q", "-c", "develop")
    git(repo, "push", "-q", "-u", "origin", "develop")
    other = clone(remote, tmp_path / "o")
    git(other, "switch", "-q", "develop")
    commit_file(other, "dev.txt", "d", "feat: dev work")
    git(other, "push", "-q")
    git(repo, "switch", "-q", "-c", "feature")
    commit_file(repo, "f.txt", "f", "feat: feature")
    assistant = assistant_for(repo)
    write(repo, "f.txt", "dirty")
    turn = assistant.handle("Atualize minha branch com develop.")
    assert turn.kind == "clarify" and "alterações locais" in turn.message
    assert (repo / "f.txt").read_text() == "dirty"  # never discarded
    git(repo, "checkout", "--", "f.txt")
    turn = assistant.handle("Atualize minha branch com develop.")
    assert turn.kind == "clarify" and "merge ou rebase" in turn.message
    turn = assistant.handle("rebase")
    ops = [s.op for s in turn.prepared.plan.steps]
    assert ops == ["fetch", "rebase"]
    assert turn.prepared.plan.steps[1].args["upstream"] == "origin/develop"
    result = confirm(assistant, turn)
    assert result.ok and (repo / "dev.txt").exists()


def test_scenario5_push_explains_and_sets_upstream(remote_repo):
    repo, remote = remote_repo
    git(repo, "switch", "-q", "-c", "feature/x")
    commit_file(repo, "x.txt", "x", "feat: x")
    assistant = assistant_for(repo)
    turn = assistant.handle("Envie minhas alterações para o remoto.")
    preview = turn.prepared.previews[0]
    assert preview.step.args == {"remote": "origin", "branch": "feature/x", "set_upstream": True,
                                 "force": False, "force_with_lease": False}
    assert any("feat: x" in c for c in preview.impact.commits)
    assert turn.prepared.needs_confirmation
    result = confirm(assistant, turn)
    assert result.ok and git(remote, "rev-parse", "feature/x").returncode == 0


def test_scripted_llm_pipeline(repo):
    fake = FakeOllama()
    fake.replies.extend([
        {"related": False},  # other.txt (form.html matches "formulário" by keyword)
        {"message": "feat(form): add signup form"},
    ])
    write(repo, "form.html", "<form>")
    write(repo, "other.txt", "x")
    assistant = assistant_for(repo, fake.client())
    turn = assistant.handle("prepara um commit do formulário que fiz")
    assert turn.interpretation.source == "router"  # commits are routed deterministically
    assert turn.prepared.plan.steps[0].args["paths"] == ["form.html"]
    assert turn.prepared.plan.steps[1].args["message"] == "feat(form): add signup form"
    assert len(turn.metrics) == 1  # drafting used the model
    assert confirm(assistant, turn).ok


def test_user_session_regressions(remote_repo):
    """Replays a real session: wrong-branch commit, repo-name topic, push dropped."""
    repo, _ = remote_repo
    git(repo, "branch", "develop")
    write(repo, "README.md", "changed")
    write(repo, "src/a.py", "new")
    assistant = assistant_for(repo)
    turn = assistant.handle("de git add commit e push das alteraçãoe que foram feitas")
    assert [s.op for s in turn.prepared.plan.steps] == ["add", "commit", "push"]
    assert turn.prepared.plan.steps[0].args["paths"] == ["README.md"]  # tracked; generic
    before = head(repo)
    turn = assistant.handle(f"de commit e push das alterações do {repo.name} para develop")
    assert turn.kind == "clarify" and "main" in turn.message and "develop" in turn.message
    assert head(repo) == before and not assistant.executor.state().staged
    turn = assistant.handle(f"commit das alterações do {repo.name}")
    assert turn.kind == "confirm" and turn.prepared.plan.steps[0].args["paths"] == ["README.md"]


def test_llm_message_must_be_conventional(repo):
    fake = FakeOllama()
    fake.replies.append({"action": "execute", "reply": "ok", "steps": [
        {"op": "commit_changes", "args": {"message": "Commit com alterações"}}]})
    write(repo, "README.md", "x")
    assistant = assistant_for(repo, fake.client())
    fake.replies.append({"message": "docs: update readme"})
    turn = assistant.handle("salva isso aí por favor")
    message = turn.prepared.plan.steps[-1].args["message"]
    assert message == "docs: update readme"
    assert any("Conventional" in w for w in turn.prepared.plan.warnings)


def test_scripted_llm_destructive_proposal_still_gated(repo):
    fake = FakeOllama()
    fake.replies.append({"action": "execute", "reply": "limpando",
                         "steps": [{"op": "reset", "args": {"mode": "hard",
                                                            "target": "HEAD"}}]})
    write(repo, "README.md", "work")
    assistant = assistant_for(repo, fake.client())
    turn = assistant.handle("arruma aí")
    assert turn.kind == "confirm" and turn.prepared.risk.label == "alto"
    assert (repo / "README.md").read_text() == "work"
    assert assistant.decide(turn.prepared, Decision.CANCEL) is None
    assert (repo / "README.md").read_text() == "work"


def test_fixes_from_second_session(remote_repo, tmp_path):
    """push preview after commit, repaired model message, remote fallback, stale branch."""
    repo, _remote = remote_repo
    # stale develop: created at init, main moves on
    git(repo, "branch", "develop")
    commit_file(repo, "src/core.py", "x", "feat: core")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "switch", "-q", "develop")
    git(repo, "push", "-q", "-u", "origin", "develop")
    for name in ("docs/guide.md", "tests/test_a.py", "tests/test_b.py", "CHANGELOG.md",
                 ".github/workflows/ci.yml"):
        write(repo, name, "x")
    fake = FakeOllama()
    fake.replies.append({"message": "Add initial documentation and CI infrastructure"})
    assistant = assistant_for(repo, fake.client())
    turn = assistant.handle("faça commit de tudo e push")
    commit = turn.prepared.plan.steps[1].args["message"]
    assert commit == "chore: add initial documentation and CI infrastructure"
    push = turn.prepared.previews[2].impact
    assert "passo anterior" in push.consequences[0] and "nada novo" not in " ".join(push.warnings)
    assert any("não contém 1 commit(s) de main" in w for w in turn.prepared.plan.warnings)
    # remote-only branch name resolves to origin/<name>
    git(repo, "branch", "-D", "main")
    turn = assistant_for(repo).handle("faça merge da main")
    assert turn.prepared.plan.steps[0].args["rev"] == "origin/main"
    assert any("origin/main" in n for n in turn.notes)


def test_commit_kind_majority():
    from grippo.llm.commit import commit_kind

    assert commit_kind(["tests/a.py", "tests/b.py", "tests/c.py", "tests/d.py", "README.md"],
                       set()) == "test"
    assert commit_kind(["docs/a.md", "tests/a.py", "src/x.py"], set()) == "chore"
    assert commit_kind(["src/x.py"], {"src/x.py"}) == "feat"
    assert commit_kind(["CHANGELOG.md", "LICENSE"], set()) == "docs"
