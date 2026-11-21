"""Authorization is enforced by code, not by the LLM. All repos here are disposable."""

from pathlib import Path

import pytest

from grippo.engine.operations import CATALOG, Risk
from grippo.engine.permissions import Decision
from grippo.engine.plan import Plan, Step
from grippo.errors import AuthorizationError, ValidationError
from grippo.git.runner import (
    CommandRejectedError,
    MutationTicket,
    _issue_ticket,
    is_read_only,
    run_git,
)
from tests.conftest import commit_file, git, head, make_executor, run_op, subject, write

pytestmark = pytest.mark.security


@pytest.fixture
def busy_repo(remote_repo):
    repo, _remote = remote_repo
    git(repo, "branch", "merged")
    git(repo, "switch", "-q", "-c", "unmerged")
    commit_file(repo, "u.txt", "u", "feat: u")
    git(repo, "switch", "-q", "main")
    commit_file(repo, "second.txt", "2", "feat: second")
    write(repo, "README.md", "local change")
    write(repo, "junk.tmp", "junk")
    git(repo, "stash", "push", "-q", "-m", "kept")  # stash@{0}
    write(repo, "README.md", "local change")
    return repo


PROTECTED = [
    ("commit", {"message": "feat: x", "allow_empty": True}),
    ("revert", {"commit": "HEAD"}),
    ("reset", {"mode": "soft", "target": "HEAD~1"}),
    ("reset", {"mode": "hard", "target": "HEAD"}),
    ("rebase", {"upstream": "merged"}),
    ("cherry_pick", {"commits": ["unmerged"]}),
    ("push", {}),
    ("push", {"force": True}),
    ("branch_delete", {"name": "merged"}),
    ("branch_delete", {"name": "unmerged", "force": True}),
    ("clean", {}),
    ("restore", {"paths": ["README.md"]}),
    ("checkout", {"rev": "merged"}),
    ("switch", {"branch": "merged"}),
    ("add", {"paths": ["README.md"]}),
    ("stash_push", {}),
    ("stash_drop", {"index": 0}),
    ("merge", {"rev": "unmerged"}),
    ("tag_create", {"name": "v9"}),
    ("remote_remove", {"name": "origin"}),
]


@pytest.mark.parametrize("op,args", PROTECTED, ids=[f"{o}-{i}" for i, (o, _) in
                                                    enumerate(PROTECTED)])
def test_protected_operations_denied_without_authorization(busy_repo, op, args):
    executor = make_executor(busy_repo)
    before = executor.state().fingerprint
    before_head = head(busy_repo)
    prepared = executor.prepare(executor.build_plan([(op, args)]))
    assert prepared.needs_confirmation
    with pytest.raises(AuthorizationError):
        executor.execute(prepared)
    assert executor.state().fingerprint == before and head(busy_repo) == before_head


@pytest.mark.parametrize("op,args", PROTECTED[:6])
def test_cancel_changes_nothing(busy_repo, op, args):
    executor = make_executor(busy_repo)
    before = executor.state().fingerprint
    prepared = executor.prepare(executor.build_plan([(op, args)]))
    assert executor.authorize(prepared, Decision.CANCEL) is None
    with pytest.raises(AuthorizationError):
        executor.execute(prepared)
    assert executor.state().fingerprint == before


def test_every_mutating_operation_needs_confirmation_at_level2(repo):
    executor = make_executor(repo)
    perms = executor.permissions
    for op in CATALOG.values():
        if op.mutating and op.risk >= Risk.MODERATE:
            assert perms.requirement([(op.name, op.risk, True)]).needs_confirmation, op.name


def test_high_risk_confirmed_even_at_level3(repo):
    executor = make_executor(repo, autonomy=3, auto_approve=list(CATALOG))
    write(repo, "README.md", "x")
    prepared = executor.prepare(executor.build_plan([("reset", {"mode": "hard"})]))
    assert prepared.needs_confirmation
    with pytest.raises(AuthorizationError):
        executor.execute(prepared)
    auto = executor.prepare(executor.build_plan([("add", {"paths": ["README.md"]})]))
    assert not auto.needs_confirmation and executor.execute(auto).ok


@pytest.mark.parametrize("level", [0, 1])
def test_low_autonomy_blocks_mutations_even_with_confirmation(repo, level):
    executor = make_executor(repo, autonomy=level)
    write(repo, "a.txt", "a")
    prepared = executor.prepare(executor.build_plan([("add", {"paths": ["a.txt"]})]))
    assert not prepared.allowed
    with pytest.raises(AuthorizationError):
        executor.permissions.grant(prepared, Decision.CONFIRM)
    assert executor.run_read("status").ok  # reads still allowed


# ----------------------------------------------------------------- runner-level bypass

@pytest.mark.parametrize("argv", [
    ["commit", "-m", "x"], ["reset", "--hard"], ["clean", "-fdx"], ["clean", "-f", "-d"],
    ["push", "--force", "origin", "main"], ["branch", "-D", "x"], ["branch", "x"],
    ["tag", "-d", "v1"], ["tag", "v1"], ["stash"], ["stash", "drop"], ["remote", "add", "x", "y"],
    ["config", "user.name", "x"], ["reflog", "expire", "--all"], ["branch", "--list", "-D", "x"],
    ["checkout", "--", "README.md"], ["restore", "README.md"],
])
def test_runner_refuses_mutation_without_ticket(repo, argv):
    before = head(repo)
    with pytest.raises(CommandRejectedError):
        run_git(repo, argv, timeout=10)
    assert head(repo) == before


@pytest.mark.parametrize("argv", [
    ["-c", "core.pager=sh", "log"], ["-C", "/", "status"], ["--git-dir=/tmp", "status"],
    ["!sh", "-c", "id"], ["sh", "-c", "id"], ["status;", "rm", "-rf", "/"], ["my-alias"],
    ["fetch", "--upload-pack=touch /tmp/pwned", "origin"], ["log", "--output=/tmp/pwned"],
    ["diff", "--ext-diff"], ["rebase", "--exec", "touch x", "main"], ["rebase", "-x", "id", "a"],
    ["add", "-p"], ["add", "--interactive"], ["clean", "-i"], ["commit", "--edit"],
    ["ls-remote", "--upload-pack=id", "x"], ["push", "--receive-pack=id", "origin"],
])
def test_runner_rejects_injection_and_dangerous_flags(repo, argv):
    with pytest.raises(CommandRejectedError):
        run_git(repo, argv, timeout=10, ticket=_issue_ticket(argv))


def test_aliases_cannot_shadow_or_be_called(repo):
    git(repo, "config", "alias.st", "!touch pwned-by-alias")
    git(repo, "config", "alias.status", "!touch pwned-status")  # git ignores builtin shadowing
    with pytest.raises(CommandRejectedError):
        run_git(repo, ["st"], timeout=10)
    assert run_git(repo, ["status"], timeout=10).ok
    assert not (repo / "pwned-by-alias").exists() and not (repo / "pwned-status").exists()


def test_tickets_cannot_be_forged_reused_or_retargeted(repo):
    with pytest.raises(CommandRejectedError):
        MutationTicket(["reset", "--hard"], object())
    write(repo, "a.txt", "a")
    ticket = _issue_ticket(["add", "--", "a.txt"])
    with pytest.raises(CommandRejectedError):
        run_git(repo, ["reset", "--hard"], timeout=10, ticket=ticket)
    ticket = _issue_ticket(["add", "--", "a.txt"])
    assert run_git(repo, ["add", "--", "a.txt"], timeout=10, ticket=ticket).ok
    with pytest.raises(CommandRejectedError):
        run_git(repo, ["add", "--", "a.txt"], timeout=10, ticket=ticket)


@pytest.mark.parametrize("sub,args,expected", [
    ("branch", ["--list", "-vv"], True), ("branch", ["-d", "x"], False),
    ("branch", ["--list", "-a"], True), ("branch", ["x"], False),
    ("tag", ["--list"], True), ("tag", ["-l", "-d", "x"], False),
    ("clean", ["-n", "-d"], True), ("clean", ["-nfd"], False), ("clean", ["-fdx"], False),
    ("stash", ["list"], True), ("stash", ["pop"], False), ("stash", [], False),
    ("remote", ["-v"], True), ("remote", ["remove", "o"], False),
    ("config", ["--get", "user.name"], True), ("config", ["--get", "--unset", "a"], False),
    ("reflog", ["show"], True), ("reflog", ["delete", "x"], False),
    ("symbolic-ref", ["HEAD"], True), ("symbolic-ref", ["HEAD", "refs/heads/x"], False),
])
def test_read_only_classification(sub, args, expected):
    assert is_read_only(sub, args) is expected


# ----------------------------------------------------------------- argument-level bypass

@pytest.mark.parametrize("op,args", [
    ("branch_create", {"name": "x; rm -rf ~"}),
    ("branch_create", {"name": "--force"}),
    ("branch_create", {"name": "$(touch pwned)"}),
    ("branch_delete", {"name": "-D"}),
    ("switch", {"branch": "-"}),
    ("checkout", {"rev": "--orphan"}),
    ("add", {"paths": ["../../etc/passwd"]}),
    ("add", {"paths": [".git/config"]}),
    ("add", {"paths": ["/etc/passwd"]}),
    ("remote_add", {"name": "x", "url": "ext::sh -c touch% /tmp/pwned"}),
    ("remote_add", {"name": "-x", "url": "https://example.invalid/r"}),
    ("reset", {"mode": "keep", "target": "HEAD"}),
    ("reset", {"mode": "hard", "target": "HEAD; rm -rf /"}),
    ("cherry_pick", {"commits": ["--strategy=ours"]}),
    ("push", {"remote": "origin --force"}),
    ("push", {"branch": "main:refs/heads/other"}),
    ("commit", {"message": ""}),
    ("log", {"n": 10_000}),
    ("add", {"paths": ["a.txt"], "exec": "id"}),
    ("does_not_exist", {}),
])
def test_malicious_arguments_rejected(repo, op, args):
    executor = make_executor(repo)
    with pytest.raises(ValidationError):
        executor.build_plan([(op, args)])


def test_shell_metacharacters_stay_literal(repo):
    executor = make_executor(repo)
    write(repo, "a.txt", "a")
    message = "feat: $(touch pwned) `touch pwned2` ; touch pwned3 && echo | cat"
    result = run_op(executor, "add", paths=["a.txt"])
    assert result.ok
    plan = executor.build_plan([("commit", {"message": message})])
    prepared = executor.prepare(plan)
    assert executor.execute(prepared, executor.authorize(prepared, Decision.CONFIRM)).ok
    assert subject(repo) == message
    for name in ("pwned", "pwned2", "pwned3"):
        assert not (repo / name).exists() and not (Path.cwd() / name).exists()


def test_handcrafted_plan_with_unnormalized_args_rejected(repo):
    executor = make_executor(repo, autonomy=3, auto_approve=["add"])
    write(repo, "a.txt", "a")
    plan = Plan((Step("add", {"paths": ["../outside"], "allow_sensitive": False}),))
    with pytest.raises((AuthorizationError, ValidationError)):
        executor.prepare(plan)


def test_sensitive_files_need_explicit_high_risk_opt_in(repo):
    executor = make_executor(repo)
    write(repo, ".env", "TOKEN=1")
    prepared = executor.prepare(executor.build_plan([("add", {"paths": [".env"]})]))
    assert any("sensível" in e for e in prepared.errors)
    with pytest.raises(AuthorizationError):
        executor.permissions.grant(prepared, Decision.CONFIRM)
    opt_in = executor.prepare(executor.build_plan([("add", {"paths": [".env"],
                                                            "allow_sensitive": True})]))
    assert opt_in.risk is Risk.HIGH and not opt_in.errors


def test_audit_records_denials_and_decisions(repo, tmp_path):
    audit_dir = tmp_path / "audit"
    executor = make_executor(repo, audit_dir=audit_dir)
    write(repo, "a.txt", "a")
    prepared = executor.prepare(executor.build_plan([("add", {"paths": ["a.txt"]})]))
    with pytest.raises(AuthorizationError):
        executor.execute(prepared)
    executor.authorize(prepared, Decision.CANCEL)
    auth = executor.authorize(prepared, Decision.CONFIRM)
    executor.execute(prepared, auth)
    executor.audit.close()
    text = (audit_dir / "audit.jsonl").read_text()
    assert '"event": "denied"' in text and '"decision": "cancel"' in text
    assert '"decision": "confirm"' in text and '"status": "done"' in text
    assert '"repo":' in text and '"branch": "main"' in text and '"risk": "moderado"' in text


def test_audit_redacts_secrets(repo, tmp_path):
    audit_dir = tmp_path / "audit"
    executor = make_executor(repo, audit_dir=audit_dir)
    url = "https://user:ghp_abcdefghijklmnopqrstuvwxyz0123@example.invalid/r.git"
    assert run_op(executor, "remote_add", name="sec", url=url).ok
    executor.audit.close()
    text = (audit_dir / "audit.jsonl").read_text()
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123" not in text and "***@" in text
