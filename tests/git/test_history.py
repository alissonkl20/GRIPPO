import pytest

from grippo.engine.operations import Risk
from grippo.engine.plan import StepStatus
from tests.conftest import commit_file, git, head, run_op, subject, write

pytestmark = pytest.mark.git


@pytest.fixture
def feature(repo):
    git(repo, "switch", "-q", "-c", "feature")
    sha = commit_file(repo, "feature.txt", "f\n", "feat: feature file")
    git(repo, "switch", "-q", "main")
    return sha


def _conflict_branches(repo):
    git(repo, "switch", "-q", "-c", "other")
    other = commit_file(repo, "README.md", "other\n", "docs: other")
    git(repo, "switch", "-q", "main")
    commit_file(repo, "README.md", "main\n", "docs: main")
    return other


def test_cherry_pick(repo, executor, feature):
    result = run_op(executor, "cherry_pick", commits=[feature[:8]])
    assert result.ok and subject(repo) == "feat: feature file"


def test_cherry_pick_conflict_then_abort(repo, executor):
    other = _conflict_branches(repo)
    before = head(repo)
    result = run_op(executor, "cherry_pick", commits=[other])
    assert result.status is StepStatus.CONFLICT
    assert result.final_state.in_progress == "cherry-pick"
    assert "README.md" in result.final_state.conflicted
    assert run_op(executor, "cherry_pick_abort").ok
    assert head(repo) == before and executor.state().in_progress is None


def test_revert_creates_new_commit(repo, executor, feature):
    git(repo, "merge", "-q", "--ff-only", "feature")
    before = head(repo)
    prepared = executor.prepare(executor.build_plan([("revert", {"commit": "HEAD"})]))
    assert "NOVO commit" in prepared.previews[0].impact.consequences[0]
    result = run_op(executor, "revert", commit="HEAD")
    assert result.ok and subject(repo).startswith("Revert")
    assert git(repo, "rev-parse", "HEAD~1").stdout.strip() == before
    assert not (repo / "feature.txt").exists()


@pytest.mark.parametrize("mode", ["soft", "mixed", "hard"])
def test_reset_modes(repo, executor, mode):
    first = head(repo)
    commit_file(repo, "b.txt", "b", "feat: b")
    if mode == "hard":
        write(repo, "README.md", "uncommitted")
    prepared = executor.prepare(executor.build_plan([("reset", {"mode": mode,
                                                               "target": "HEAD~1"})]))
    assert prepared.risk is (Risk.HIGH if mode == "hard" else Risk.MODERATE)
    if mode == "hard":
        assert "README.md" in prepared.previews[0].impact.files
        assert any("NÃO podem ser recuperados" in w
                   for w in prepared.previews[0].impact.warnings)
    result = run_op(executor, "reset", mode=mode, target="HEAD~1")
    assert result.ok and head(repo) == first
    state = result.final_state
    if mode == "soft":
        assert "b.txt" in state.staged
    elif mode == "mixed":
        assert "b.txt" in state.untracked
    else:
        assert state.clean and not (repo / "b.txt").exists()


def test_reflog_recovers_after_reset_hard(repo, executor):
    lost = commit_file(repo, "b.txt", "b", "feat: precious")
    assert run_op(executor, "reset", mode="hard", target="HEAD~1").ok
    reflog = executor.run_read("reflog").results[0].stdout
    assert lost[:7] in reflog
    assert run_op(executor, "reset", mode="hard", target=lost).ok
    assert subject(repo) == "feat: precious"


def test_rebase_simple(repo, executor, feature):
    commit_file(repo, "main2.txt", "m", "feat: main moves")
    git(repo, "switch", "-q", "feature")
    prepared = executor.prepare(executor.build_plan([("rebase", {"upstream": "main"})]))
    assert any("reescrito" in c for c in prepared.previews[0].impact.consequences)
    result = run_op(executor, "rebase", upstream="main")
    assert result.ok
    assert git(repo, "merge-base", "--is-ancestor", "main", "HEAD").returncode == 0


def test_rebase_conflict_continue(repo, executor):
    _conflict_branches(repo)
    git(repo, "switch", "-q", "other")
    result = run_op(executor, "rebase", upstream="main")
    assert result.status is StepStatus.CONFLICT and result.final_state.in_progress == "rebase"
    blocked = executor.prepare(executor.build_plan([("rebase_continue", {})]))
    assert blocked.errors  # still conflicted
    write(repo, "README.md", "resolved\n")
    assert run_op(executor, "add", paths=["README.md"]).ok
    result = run_op(executor, "rebase_continue")
    assert result.ok, result.results[0].stderr
    assert executor.state().in_progress is None


def test_rebase_conflict_abort(repo, executor):
    _conflict_branches(repo)
    git(repo, "switch", "-q", "other")
    before = head(repo)
    assert run_op(executor, "rebase", upstream="main").status is StepStatus.CONFLICT
    assert run_op(executor, "rebase_abort").ok
    assert head(repo) == before


def test_rebase_requires_clean_tree(repo, executor, feature):
    git(repo, "switch", "-q", "feature")
    write(repo, "feature.txt", "dirty")
    prepared = executor.prepare(executor.build_plan([("rebase", {"upstream": "main"})]))
    assert any("limpo" in e for e in prepared.errors)


def test_merge_and_conflict_abort(repo, executor, feature):
    assert run_op(executor, "merge", rev="feature").ok
    assert (repo / "feature.txt").exists()
    _conflict_branches(repo)
    before = head(repo)
    result = run_op(executor, "merge", rev="other")
    assert result.status is StepStatus.CONFLICT and result.final_state.in_progress == "merge"
    assert run_op(executor, "merge_abort").ok
    assert head(repo) == before and executor.state().clean


def test_merge_conflict_resolve_continue(repo, executor):
    _conflict_branches(repo)
    assert run_op(executor, "merge", rev="other").status is StepStatus.CONFLICT
    write(repo, "README.md", "merged\n")
    assert run_op(executor, "add", paths=["README.md"]).ok
    assert run_op(executor, "merge_continue").ok
    assert git(repo, "rev-list", "--parents", "-n1", "HEAD").stdout.count(" ") == 2


def test_stash_and_pop(repo, executor):
    write(repo, "README.md", "work in progress")
    write(repo, "new.txt", "untracked")
    result = run_op(executor, "stash_push", message="wip", include_untracked=True)
    assert result.ok and result.final_state.clean and result.final_state.stash_count == 1
    result = run_op(executor, "stash_pop")
    assert result.ok and (repo / "new.txt").exists()
    assert (repo / "README.md").read_text() == "work in progress"


def test_stash_pop_conflict_keeps_stash(repo, executor):
    write(repo, "README.md", "stashed version")
    assert run_op(executor, "stash_push").ok
    commit_file(repo, "README.md", "committed version", "docs: change")
    result = run_op(executor, "stash_pop")
    assert result.status in (StepStatus.CONFLICT, StepStatus.FAILED)
    assert executor.state().stash_count == 1  # git keeps the stash on conflict


def test_clean_lists_files_and_is_high(repo, executor):
    write(repo, "junk/a.tmp", "a")
    write(repo, "b.tmp", "b")
    prepared = executor.prepare(executor.build_plan([("clean", {})]))
    assert prepared.risk is Risk.HIGH
    assert set(prepared.previews[0].impact.files) == {"junk/", "b.tmp"}
    assert run_op(executor, "clean").ok
    assert not (repo / "b.tmp").exists() and (repo / "README.md").exists()


def test_restore_discards_only_listed(repo, executor):
    commit_file(repo, "x.txt", "x", "feat: x")
    write(repo, "x.txt", "changed")
    write(repo, "README.md", "also changed")
    assert run_op(executor, "restore", paths=["x.txt"]).ok
    assert (repo / "x.txt").read_text() == "x"
    assert (repo / "README.md").read_text() == "also changed"
