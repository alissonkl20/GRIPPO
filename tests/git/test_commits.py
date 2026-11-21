import pytest

from grippo.agent.planner import Planner
from grippo.agent.router import Interpretation
from grippo.engine.plan import StepStatus
from grippo.llm.commit import CommitDrafter
from tests.conftest import git, init_repo, make_executor, run_ops, subject, write

pytestmark = pytest.mark.git


def _plan_commit(executor, request=""):
    planner = Planner(executor, CommitDrafter(None))
    interp = Interpretation("execute", [("commit_changes", {"request": request})])
    return planner.plan(interp, executor.state())


def test_simple_and_conventional(repo, executor):
    write(repo, "a.txt", "a")
    assert run_ops(executor, ("add", {"paths": ["a.txt"]}),
                   ("commit", {"message": "feat(core): add a"})).ok
    assert subject(repo) == "feat(core): add a"


def test_partial_commit_only_selected_files(repo, executor):
    write(repo, "keep.txt", "k")
    write(repo, "later.txt", "l")
    assert run_ops(executor, ("add", {"paths": ["keep.txt"]}),
                   ("commit", {"message": "feat: keep"})).ok
    files = git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert files == ["keep.txt"]
    assert "later.txt" in executor.state().untracked


def test_planner_commits_only_staged_when_something_is_staged(repo, executor):
    write(repo, "staged.txt", "s")
    write(repo, "README.md", "unstaged change")
    git(repo, "add", "staged.txt")
    outcome = _plan_commit(executor)
    assert outcome.kind == "plan"
    assert [s.op for s in outcome.plan.steps] == ["commit"]
    assert any("staged" in w for w in outcome.plan.warnings)


def test_planner_staged_unstaged_untracked_unrelated(repo, executor):
    write(repo, "src/form/signup.html", "<form>")
    write(repo, "src/form/signup.js", "js")
    write(repo, "src/report/chart.js", "unrelated")
    write(repo, ".env", "TOKEN=abc")
    outcome = _plan_commit(executor, "formulário de signup")
    add = outcome.plan.steps[0]
    assert add.op == "add"
    assert set(add.args["paths"]) == {"src/form/"} or all("form" in p for p in add.args["paths"])
    joined = " ".join(outcome.plan.warnings)
    assert ".env" in joined and "sensíveis" in joined


def test_never_adds_everything_blindly(repo, executor):
    write(repo, "README.md", "edit")
    for i in range(5):
        write(repo, f"scratch{i}.tmp", "x")
    outcome = _plan_commit(executor)
    assert outcome.plan.steps[0].args["paths"] == ["README.md"]
    assert "." not in outcome.plan.steps[0].args["paths"]


def test_message_based_on_diff_heuristic(repo, executor):
    write(repo, "docs/guide.md", "# guide")
    outcome = _plan_commit(executor)
    message = outcome.plan.steps[-1].args["message"]
    assert message.startswith("docs")


def test_nothing_to_commit(repo, executor):
    outcome = _plan_commit(executor)
    assert outcome.kind == "noop"
    prepared = executor.prepare(executor.build_plan([("commit", {"message": "feat: x"})]))
    assert "nada staged" in " ".join(prepared.errors)


def test_failing_hook_is_reported(repo, executor):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'lint failed' >&2\nexit 1\n")
    hook.chmod(0o755)
    write(repo, "a.txt", "a")
    result = run_ops(executor, ("add", {"paths": ["a.txt"]}), ("commit", {"message": "feat: a"}))
    assert result.status is StepStatus.FAILED
    assert result.results[1].status is StepStatus.FAILED
    assert "lint failed" in result.results[1].stderr
    assert subject(repo) == "chore: init"
    assert "a.txt" in result.final_state.staged  # intermediate state reported, not hidden


def test_missing_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty-gitconfig"))
    (tmp_path / "empty-gitconfig").write_text("[user]\n\tuseConfigOnly = true\n")
    repo = init_repo(tmp_path / "noid", identity=False)
    write(repo, "a.txt", "a")
    executor = make_executor(repo)
    result = run_ops(executor, ("add", {"paths": ["a.txt"]}), ("commit", {"message": "feat: a"}))
    assert result.status is StepStatus.FAILED
    assert "identidade" in result.results[1].hint


def test_amend_unpublished_is_moderate(repo, executor):
    write(repo, "a.txt", "a")
    git(repo, "add", "a.txt")
    prepared = executor.prepare(executor.build_plan([("commit", {"message": "chore: init v2",
                                                                 "amend": True})]))
    assert prepared.risk.label == "moderado"


def test_specific_request_with_no_match_asks_instead_of_adding_all(repo, executor):
    write(repo, "src/billing.py", "x")
    write(repo, "README.md", "y")
    outcome = _plan_commit(executor, "commit do módulo de pagamentos via pix")
    assert outcome.kind == "clarify"
    assert "billing.py" in outcome.message and "commit de tudo" in outcome.message


def test_commit_preview_after_add_is_not_misleading(repo, executor):
    write(repo, "a.txt", "a")
    write(repo, "README.md", "other change")
    prepared = executor.prepare(executor.build_plan([("add", {"paths": ["a.txt"]}),
                                                     ("commit", {"message": "feat: a"})]))
    commit_impact = prepared.previews[1].impact
    assert "passo anterior" in commit_impact.consequences[0]
    assert not commit_impact.warnings and "0 arquivo" not in commit_impact.consequences[0]
