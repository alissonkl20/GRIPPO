import pytest

from grippo.agent.planner import Planner
from grippo.agent.router import Interpretation
from grippo.engine.operations import Risk
from grippo.engine.plan import StepStatus
from grippo.llm.commit import CommitDrafter
from tests.conftest import clone, commit_file, git, make_executor, run_op

pytestmark = pytest.mark.git


@pytest.fixture
def diverged(remote_repo, tmp_path):
    repo, remote = remote_repo
    other = clone(remote, tmp_path / "other")
    commit_file(other, "theirs.txt", "t", "feat: theirs")
    git(other, "push", "-q")
    commit_file(repo, "mine.txt", "m", "feat: mine")
    git(repo, "fetch", "-q")
    return repo, remote, other


def _publish(executor):
    return Planner(executor, CommitDrafter(None)).plan(
        Interpretation("execute", [("publish", {})]), executor.state())


def test_push_rejected_when_diverged(diverged):
    repo, remote, _ = diverged
    executor = make_executor(repo)
    outcome = _publish(executor)
    assert outcome.kind == "clarify" and "atrás" in outcome.message
    result = run_op(executor, "push")
    assert result.status is StepStatus.FAILED
    assert "pull" in result.results[0].hint
    assert git(remote, "log", "-1", "--format=%s", "main").stdout.strip() == "feat: theirs"


def test_pull_ff_only_fails_on_divergence_then_rebase(diverged):
    repo, _, _ = diverged
    executor = make_executor(repo)
    result = run_op(executor, "pull")
    assert result.status is StepStatus.FAILED and "divergiram" in result.results[0].hint
    assert (repo / "mine.txt").exists() and not (repo / "theirs.txt").exists()
    assert run_op(executor, "pull", mode="rebase").ok
    assert (repo / "theirs.txt").exists() and (repo / "mine.txt").exists()
    assert run_op(executor, "push").ok


def test_force_push_is_high_and_overwrites(diverged):
    repo, remote, _ = diverged
    executor = make_executor(repo)
    prepared = executor.prepare(executor.build_plan([("push", {"force": True})]))
    assert prepared.risk is Risk.HIGH
    assert any("SOBRESCREVE" in c for c in prepared.previews[0].impact.consequences)
    assert any("protegida" in w for w in prepared.previews[0].impact.warnings)
    assert run_op(executor, "push", force=True).ok
    assert git(remote, "log", "-1", "--format=%s", "main").stdout.strip() == "feat: mine"


def test_force_with_lease_refuses_stale(remote_repo, tmp_path):
    repo, remote = remote_repo
    other = clone(remote, tmp_path / "other2")
    commit_file(repo, "mine.txt", "m", "feat: mine")
    commit_file(other, "late.txt", "l", "feat: pushed after our last fetch")
    git(other, "push", "-q")  # we did NOT fetch: our lease is stale
    executor = make_executor(repo)
    result = run_op(executor, "push", force_with_lease=True)
    assert result.status is StepStatus.FAILED
    assert "force-with-lease" in result.results[0].hint or "remoto" in result.results[0].hint
    assert git(remote, "log", "-1", "--format=%s", "main").stdout.strip().startswith("feat: pushed")


def test_new_branch_without_upstream_sets_it(remote_repo):
    repo, remote = remote_repo
    git(repo, "switch", "-q", "-c", "feature/new")
    commit_file(repo, "n.txt", "n", "feat: new")
    executor = make_executor(repo)
    outcome = _publish(executor)
    assert outcome.kind == "plan"
    step = outcome.plan.steps[0]
    assert step.args["set_upstream"] is True and step.args["branch"] == "feature/new"
    prepared = executor.prepare(outcome.plan)
    from grippo.engine.permissions import Decision

    result = executor.execute(prepared, executor.authorize(prepared, Decision.CONFIRM))
    assert result.ok
    assert executor.state().upstream == "origin/feature/new"
    assert git(remote, "rev-parse", "feature/new").returncode == 0


def test_nothing_to_push(remote_repo):
    repo, _ = remote_repo
    outcome = _publish(make_executor(repo))
    assert outcome.kind == "noop"


def test_missing_remote(repo):
    executor = make_executor(repo)
    assert _publish(executor).kind == "answer"
    prepared = executor.prepare(executor.build_plan([("push", {"remote": "nope"})]))
    assert any("inexistente" in e for e in prepared.errors)


def test_remote_branch_deleted(remote_repo, tmp_path):
    repo, remote = remote_repo
    git(repo, "push", "-q", "origin", "main:refs/heads/gone")
    executor = make_executor(repo)
    assert run_op(executor, "push_delete", branch="gone").ok
    assert git(remote, "rev-parse", "--verify", "-q", "gone", check=False).returncode != 0
    result = run_op(executor, "pull", remote="origin", branch="gone")
    assert result.status is StepStatus.FAILED and "não existe" in result.results[0].hint


def test_fetch_prune(remote_repo, tmp_path):
    repo, remote = remote_repo
    git(repo, "push", "-q", "origin", "main:refs/heads/tmp")
    git(repo, "fetch", "-q")
    git(remote, "update-ref", "-d", "refs/heads/tmp")
    executor = make_executor(repo)
    assert "origin/tmp" in executor.reader.remote_branches()
    assert run_op(executor, "fetch", prune=True).ok
    assert "origin/tmp" not in executor.reader.remote_branches()


def test_tags_remote(remote_repo):
    repo, remote = remote_repo
    executor = make_executor(repo)
    assert run_op(executor, "tag_create", name="v0.1.0").ok
    assert run_op(executor, "push_tag", tag="v0.1.0").ok
    assert "v0.1.0" in git(remote, "tag").stdout
    prepared = executor.prepare(executor.build_plan([("tag_delete_remote", {"tag": "v0.1.0"})]))
    assert prepared.risk is Risk.HIGH
    assert run_op(executor, "tag_delete_remote", tag="v0.1.0").ok
    assert "v0.1.0" not in git(remote, "tag").stdout


def test_rebase_of_published_commits_is_high(remote_repo):
    repo, _ = remote_repo
    git(repo, "switch", "-q", "-c", "topic")
    commit_file(repo, "t.txt", "t", "feat: topic")
    git(repo, "push", "-q", "-u", "origin", "topic")
    git(repo, "switch", "-q", "main")
    commit_file(repo, "m.txt", "m", "feat: main")
    git(repo, "switch", "-q", "topic")
    executor = make_executor(repo)
    prepared = executor.prepare(executor.build_plan([("rebase", {"upstream": "main"})]))
    assert prepared.risk is Risk.HIGH
    assert any("force push" in w for w in prepared.previews[0].impact.warnings)
    soft = executor.prepare(executor.build_plan([("reset", {"mode": "soft",
                                                            "target": "HEAD~1"})]))
    assert soft.risk is Risk.HIGH  # removing published commits
