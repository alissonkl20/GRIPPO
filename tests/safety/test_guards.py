"""Guards against irreversible mistakes: no-ops, protected branches, grounding, backups."""

import pytest

from grippo.engine.backup import list_backups
from grippo.engine.guard import grounding_problems
from grippo.engine.plan import Plan, Step, StepStatus
from grippo.errors import AuthorizationError
from grippo.git.runner import CommandRejectedError, run_git
from tests.conftest import commit_file, git, head, make_executor, run_ops, write

pytestmark = pytest.mark.git


def _errors(executor, op, **args):
    return executor.prepare(executor.build_plan([(op, args)])).errors


# ----------------------------------------------------------------------------- no-ops
def test_rebase_onto_current_branch_is_noop(repo):
    executor = make_executor(repo)
    errors = _errors(executor, "rebase", upstream="main")
    assert errors and "nada a fazer" in errors[0]


def test_rebase_when_already_based_is_noop(repo):
    git(repo, "switch", "-q", "-c", "feat")
    commit_file(repo, "a.txt", "a", "feat: a")
    errors = _errors(make_executor(repo), "rebase", upstream="main")
    assert errors and "já está baseada" in errors[0]


def test_merge_of_contained_branch_is_noop(repo):
    git(repo, "branch", "old")
    commit_file(repo, "a.txt", "a", "feat: a")
    errors = _errors(make_executor(repo), "merge", rev="old")
    assert errors and "já está contido" in errors[0]
    assert "nela mesma" in _errors(make_executor(repo), "merge", rev="main")[0]


def test_soft_reset_to_head_is_noop(repo):
    errors = _errors(make_executor(repo), "reset", mode="soft", target="HEAD")
    assert errors and "nada a fazer" in errors[0]


def test_protected_branch_cannot_be_deleted(repo):
    git(repo, "switch", "-q", "-c", "feat")
    errors = _errors(make_executor(repo), "branch_delete", name="main")
    assert errors and "protegida" in errors[0]


# ----------------------------------------------------------------------------- grounding
@pytest.mark.parametrize(
    "op,args,request_text,grounded",
    [
        ("branch_delete", {"name": "feature/old"}, "exclua a branch feature/old", True),
        ("branch_delete", {"name": "feature/old"}, "limpe minhas branches", False),
        ("revert", {"commit": "abc1234"}, "reverta o commit abc1234", True),
        ("revert", {"commit": "abc1234"}, "reverta o último commit", False),
        ("revert", {"commit": "HEAD"}, "reverta o último commit", True),
        ("rebase", {"upstream": "origin/main"}, "faça rebase com main", True),
        ("rebase", {"upstream": "develop"}, "faça rebase com main", False),
        ("reset", {"mode": "hard", "target": "HEAD"}, "arruma aí", False),
        (
            "reset",
            {"mode": "hard", "target": "HEAD~1"},
            "descarte tudo, reset hard no anterior",
            True,
        ),
        ("reset", {"mode": "soft", "target": "HEAD~1"}, "desfaça o último commit", True),
        ("reset", {"mode": "soft", "target": "HEAD~9"}, "desfaça o último commit", False),
        ("branch_delete", {"name": "x", "force": True}, "apague a branch x", False),
        ("branch_delete", {"name": "x", "force": True}, "apague a branch x mesmo assim", True),
        ("clean", {"directories": True, "ignored": False}, "arruma o projeto", False),
        (
            "clean",
            {"directories": True, "ignored": False},
            "limpe os arquivos não rastreados",
            True,
        ),
        (
            "clean",
            {"directories": True, "ignored": True},
            "limpe os arquivos não rastreados",
            False,
        ),
        ("clean", {"directories": True, "ignored": True}, "limpe tudo, inclusive ignorados", True),
        ("stash_drop", {"index": 0}, "apague o stash", True),
        ("stash_drop", {"index": 3}, "apague o stash", False),
        ("stash_drop", {"index": 3}, "apague o stash 3", True),
        ("stash_drop", {"index": 0}, "arruma aí", False),
        ("tag_delete_remote", {"tag": "v1"}, "apague a tag v1 do remoto", True),
        ("tag_delete_remote", {"tag": "v1"}, "apague as tags do remoto", False),
    ],
)
def test_grounding(op, args, request_text, grounded):
    plan = Plan((Step(op, args),))
    assert (grounding_problems(plan, request_text) == []) is grounded


def test_force_flags_need_explicit_words():
    plan = Plan((Step("push", {"force": True, "branch": "main"}),))
    assert grounding_problems(plan, "envie para o remoto")
    assert not grounding_problems(plan, "envie para o remoto com force")


# ----------------------------------------------------------------------------- backups
def test_hard_reset_creates_backup_including_uncommitted_work(repo):
    commit_file(repo, "a.txt", "v1", "feat: a")
    write(repo, "a.txt", "trabalho nao commitado")
    executor = make_executor(repo)
    result = run_ops(executor, ("reset", {"mode": "hard", "target": "HEAD~1"}))
    assert result.status is StepStatus.DONE
    assert "Backup de segurança" in result.message
    refs = {ref.rsplit("/", 1)[-1]: sha for ref, sha, _ in list_backups(repo)}
    wip = next(sha for name, sha in refs.items() if name.endswith("-wip"))
    assert git(repo, "show", f"{wip}:a.txt").stdout == "trabalho nao commitado"
    assert not (repo / "a.txt").exists()  # the reset really happened


def test_branch_delete_keeps_tip_reachable(repo):
    git(repo, "switch", "-q", "-c", "feat")
    tip = commit_file(repo, "a.txt", "a", "feat: a")
    git(repo, "switch", "-q", "main")
    result = run_ops(make_executor(repo), ("branch_delete", {"name": "feat", "force": True}))
    assert result.status is StepStatus.DONE
    assert any(sha == tip[:7] or tip.startswith(sha) for _, sha, _ in list_backups(repo))
    assert git(repo, "cat-file", "-t", tip).stdout.strip() == "commit"


def test_non_destructive_ops_make_no_backup(repo):
    run_ops(make_executor(repo), ("branch_create", {"name": "x"}))
    assert list_backups(repo) == []


def test_update_ref_is_restricted_to_backup_refs(repo):
    sha = head(repo)
    for argv in (
        ["update-ref", "refs/heads/main", sha],
        ["update-ref", "-d", "refs/grippo/backup/x"],
        ["update-ref", "refs/grippo/backup/../../heads/main", sha],
        ["update-ref", "refs/grippo/backup/x", "HEAD"],
    ):
        with pytest.raises((CommandRejectedError, AuthorizationError)):
            run_git(repo, argv, timeout=5)


def test_backup_names_are_unique_and_valid_for_long_branches(repo):
    long_name = "feature/" + "x" * 80
    git(repo, "switch", "-q", "-c", long_name)
    commit_file(repo, "a.txt", "a", "feat: a")
    executor = make_executor(repo)
    for _ in range(2):  # same second, same branch, same op
        git(repo, "reset", "-q", "--soft", "HEAD")
        write(repo, "b.txt", "wip")
        result = run_ops(executor, ("reset", {"mode": "hard", "target": "HEAD"}))
        assert result.status is StepStatus.DONE, result.message
    refs = [ref for ref, _, _ in list_backups(repo)]
    assert len(refs) == len(set(refs)) >= 2
    assert all(len(ref) < 150 and ref.isascii() for ref in refs)


def test_stash_and_tag_deletion_keep_their_commits(repo):
    write(repo, "a.txt", "guardado")
    git(repo, "add", "a.txt")
    git(repo, "stash", "push", "-q")
    stash_sha = git(repo, "rev-parse", "stash@{0}").stdout.strip()
    result = run_ops(make_executor(repo), ("stash_drop", {"index": 0}))
    assert result.status is StepStatus.DONE
    assert any(stash_sha.startswith(sha) for _, sha, _ in list_backups(repo))
    assert git(repo, "cat-file", "-t", stash_sha).stdout.strip() == "commit"


def test_clean_is_not_presented_as_backed_up(repo):
    write(repo, "lixo.tmp", "x")
    result = run_ops(make_executor(repo), ("clean", {"directories": True, "ignored": False}))
    assert result.status is StepStatus.DONE
    assert "Backup" not in result.message and list_backups(repo) == []


def test_rebase_is_not_a_noop_when_it_would_flatten_merges(repo):
    git(repo, "switch", "-q", "-c", "feat")
    commit_file(repo, "a.txt", "a", "feat: a")
    git(repo, "switch", "-q", "main")
    commit_file(repo, "m.txt", "m", "feat: m")
    git(repo, "switch", "-q", "feat")
    git(repo, "merge", "-q", "--no-edit", "main")  # feat now holds a merge commit
    git(repo, "branch", "-f", "base", "main")
    errors = _errors(make_executor(repo), "rebase", upstream="base")
    assert not any("nada a fazer" in error for error in errors)
