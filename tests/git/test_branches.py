import pytest

from grippo.engine.operations import Risk
from grippo.engine.plan import StepStatus
from tests.conftest import commit_file, git, make_executor, run_op, write

pytestmark = pytest.mark.git


def test_create_rename_switch_delete(repo, executor):
    result = run_op(executor, "branch_create", name="feature/a")
    assert result.ok and result.final_state.branch == "feature/a"
    assert run_op(executor, "branch_rename", new="feature/b").ok
    assert executor.state().branch == "feature/b"
    assert run_op(executor, "switch", branch="main").ok
    assert run_op(executor, "branch_delete", name="feature/b").ok
    assert "feature/b" not in git(repo, "branch").stdout


def test_create_without_switch_and_from_start(repo, executor):
    first = git(repo, "rev-parse", "HEAD").stdout.strip()
    commit_file(repo, "b.txt", "b", "feat: b")
    assert run_op(executor, "branch_create", name="old", start=first, switch=False).ok
    assert git(repo, "rev-parse", "old").stdout.strip() == first
    assert executor.state().branch == "main"


def test_existing_branch_rejected(repo, executor):
    git(repo, "branch", "dup")
    prepared = executor.prepare(executor.build_plan([("branch_create", {"name": "dup"})]))
    assert any("já existe" in e for e in prepared.errors)


def test_delete_unmerged_refused_without_force(repo, executor):
    git(repo, "switch", "-q", "-c", "wip")
    commit_file(repo, "w.txt", "w", "feat: wip")
    git(repo, "switch", "-q", "main")
    prepared = executor.prepare(executor.build_plan([("branch_delete", {"name": "wip"})]))
    assert prepared.risk is Risk.MODERATE
    assert any("não mesclados" in w for w in prepared.previews[0].impact.warnings)
    result = run_op(executor, "branch_delete", name="wip")
    assert result.status is StepStatus.FAILED
    assert "não mesclados" in result.results[0].hint
    assert "wip" in git(repo, "branch").stdout


def test_force_delete_is_high_risk_and_lists_commits(repo, executor):
    git(repo, "switch", "-q", "-c", "wip")
    commit_file(repo, "w.txt", "w", "feat: only here")
    git(repo, "switch", "-q", "main")
    prepared = executor.prepare(executor.build_plan([("branch_delete", {"name": "wip",
                                                                        "force": True})]))
    assert prepared.risk is Risk.HIGH and prepared.needs_confirmation
    assert any("only here" in c for c in prepared.previews[0].impact.commits)
    assert "git branch wip" in prepared.previews[0].impact.recovery
    assert run_op(executor, "branch_delete", name="wip", force=True).ok


def test_cannot_delete_current(repo, executor):
    prepared = executor.prepare(executor.build_plan([("branch_delete", {"name": "main"})]))
    assert prepared.errors


def test_detect_current_upstream_and_merged(remote_repo):
    repo, _ = remote_repo
    executor = make_executor(repo)
    state = executor.state()
    assert state.branch == "main" and state.upstream == "origin/main"
    git(repo, "branch", "merged-one")
    git(repo, "switch", "-q", "-c", "unmerged-one")
    commit_file(repo, "u.txt", "u", "feat: u")
    git(repo, "switch", "-q", "main")
    merged = executor.run_read("branch_list", {"merged": "HEAD"}).results[0].stdout
    unmerged = executor.run_read("branch_list", {"no_merged": "HEAD"}).results[0].stdout
    assert "merged-one" in merged and "unmerged-one" not in merged
    assert "unmerged-one" in unmerged


def test_switch_with_pending_changes_warns_and_git_refuses_conflict(repo, executor):
    git(repo, "switch", "-q", "-c", "other")
    commit_file(repo, "README.md", "other version\n", "docs: other")
    git(repo, "switch", "-q", "main")
    write(repo, "README.md", "local edit\n")
    prepared = executor.prepare(executor.build_plan([("switch", {"branch": "other"})]))
    assert any("alterações locais" in w for w in prepared.previews[0].impact.warnings)
    result = run_op(executor, "switch", branch="other")
    assert result.status is StepStatus.FAILED
    assert (repo / "README.md").read_text() == "local edit\n"  # nothing lost
    assert executor.state().branch == "main"


def test_many_branches(repo, executor):
    for i in range(15):
        assert run_op(executor, "branch_create", name=f"b{i}", switch=False).ok
    assert len(executor.reader.local_branches()) == 16
