import pytest

from grippo.engine.plan import StepStatus
from tests.conftest import clone, commit_file, git, head, make_executor, run_op, run_ops, write

pytestmark = pytest.mark.git


def test_read_operations(repo, executor):
    commit_file(repo, "src/a.py", "print(1)\n", "feat: add a")
    write(repo, "src/a.py", "print(2)\n")
    write(repo, "new.txt", "n")
    git(repo, "tag", "v0.1.0")
    git(repo, "add", "new.txt")
    expectations = {
        "status": "a.py",
        "diff": "print(2)",
        "log": "feat: add a",
        "show": "feat: add a",
        "branch_list": "main",
        "ls_files": "src/a.py",
        "reflog": "commit",
        "tag_list": "v0.1.0",
        "rev_parse": head(repo)[:12],
    }
    for op, needle in expectations.items():
        args = {"rev": "HEAD"} if op == "rev_parse" else {}
        result = executor.run_read(op, args)
        assert result.ok, (op, result.results[0].stderr)
        assert needle in result.results[0].stdout, (op, result.results[0].stdout)
    staged = executor.run_read("diff", {"staged": True})
    assert "new.txt" in staged.results[0].stdout and "print(2)" not in staged.results[0].stdout
    assert executor.run_read("remote_list").ok
    assert executor.run_read("stash_list").ok


def test_reads_do_not_mutate(repo, executor):
    write(repo, "x.txt", "x")
    before = executor.state().fingerprint
    for op in ("status", "diff", "log", "branch_list", "ls_files", "reflog", "tag_list"):
        executor.run_read(op)
    assert executor.state().fingerprint == before


def test_add_commit_and_verify(repo, executor):
    write(repo, "a.txt", "a")
    write(repo, "dir with space/b.txt", "b")
    result = run_ops(executor, ("add", {"paths": ["a.txt", "dir with space/b.txt"]}),
                     ("commit", {"message": "feat: two files"}))
    assert result.ok and all(r.status is StepStatus.DONE for r in result.results)
    assert result.final_state.clean
    assert git(repo, "log", "-1", "--format=%s").stdout.strip() == "feat: two files"


def test_switch_checkout_tag(repo, executor):
    git(repo, "branch", "develop")
    assert run_op(executor, "switch", branch="develop").final_state.branch == "develop"
    first = head(repo)
    commit_file(repo, "b.txt", "b", "feat: b")
    result = run_op(executor, "checkout", rev=first)
    assert result.ok and result.final_state.detached and result.final_state.head == first
    assert run_op(executor, "switch", branch="main").ok
    assert run_op(executor, "tag_create", name="v1.0.0", message="release").ok
    assert "v1.0.0" in git(repo, "tag").stdout
    assert run_op(executor, "tag_delete", name="v1.0.0").ok


def test_fetch_pull_push(remote_repo, tmp_path):
    repo, remote = remote_repo
    other = clone(remote, tmp_path / "other")
    commit_file(other, "o.txt", "o", "feat: from other")
    git(other, "push", "-q")
    executor = make_executor(repo)
    assert run_op(executor, "fetch").ok
    assert executor.state().behind == 1
    result = run_op(executor, "pull")
    assert result.ok and (repo / "o.txt").exists()
    commit_file(repo, "mine.txt", "m", "feat: mine")
    result = run_op(executor, "push")
    assert result.ok, result.results[0].stderr
    assert git(remote, "log", "-1", "--format=%s", "main").stdout.strip() == "feat: mine"


def test_remote_management(repo, executor, tmp_path):
    url = str(tmp_path / "x.git")
    assert run_op(executor, "remote_add", name="backup", url=url).ok
    assert run_op(executor, "remote_rename", old="backup", new="bk").ok
    assert run_op(executor, "remote_set_url", name="bk", url=url + "2").ok
    assert git(repo, "remote", "get-url", "bk").stdout.strip() == url + "2"
    assert run_op(executor, "remote_remove", name="bk").ok
    assert "bk" not in git(repo, "remote").stdout


def test_rm_mv(repo, executor):
    commit_file(repo, "old.txt", "o", "chore: old")
    assert run_op(executor, "mv", source="old.txt", destination="new.txt").ok
    assert (repo / "new.txt").exists()
    assert run_ops(executor, ("commit", {"message": "chore: rename"})).ok
    assert run_op(executor, "rm", paths=["new.txt"], cached=True).ok
    assert (repo / "new.txt").exists()
    assert "new.txt" in executor.state().untracked
