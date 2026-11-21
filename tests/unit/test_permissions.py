from dataclasses import replace

import pytest

from grippo.engine.operations import Risk
from grippo.engine.permissions import Decision, GitPermissionManager
from grippo.errors import AuthorizationError, StateChangedError
from tests.conftest import make_executor, write


def _prepared(executor, *steps):
    return executor.prepare(executor.build_plan(steps))


@pytest.mark.parametrize("level,allowed,needs", [(0, False, True), (1, False, True),
                                                 (2, True, True), (3, True, True)])
def test_requirement_by_autonomy(level, allowed, needs):
    manager = GitPermissionManager(autonomy_level=level)
    requirement = manager.requirement([("commit", Risk.MODERATE, True)])
    assert requirement.allowed is allowed and requirement.needs_confirmation is needs


def test_reads_never_need_confirmation():
    manager = GitPermissionManager(autonomy_level=0)
    requirement = manager.requirement([("status", Risk.LOW, False)])
    assert requirement.allowed and not requirement.needs_confirmation


def test_level3_auto_approves_listed_moderate_but_never_high():
    manager = GitPermissionManager(autonomy_level=3, auto_approve=["add", "commit", "reset"])
    assert not manager.requirement([("add", Risk.MODERATE, True),
                                    ("commit", Risk.MODERATE, True)]).needs_confirmation
    assert manager.requirement([("reset", Risk.HIGH, True)]).needs_confirmation
    assert manager.requirement([("push", Risk.MODERATE, True)]).needs_confirmation


@pytest.mark.security
@pytest.mark.parametrize("decision", ["confirm", "CONFIRM", True, 1, "s", None, ""])
def test_only_the_enum_authorizes(repo, decision):
    executor = make_executor(repo)
    write(repo, "a.txt", "a")
    prepared = _prepared(executor, ("add", {"paths": ["a.txt"]}))
    assert executor.permissions.grant(prepared, decision) is None  # type: ignore[arg-type]
    assert executor.permissions.grant(prepared, Decision.CANCEL) is None


@pytest.mark.security
def test_forged_signature_rejected(repo):
    executor = make_executor(repo)
    write(repo, "a.txt", "a")
    prepared = _prepared(executor, ("add", {"paths": ["a.txt"]}))
    auth = executor.permissions.grant(prepared, Decision.CONFIRM)
    forged = replace(auth, signature="0" * 64)
    with pytest.raises(AuthorizationError, match="assinatura"):
        executor.execute(prepared, forged)


@pytest.mark.security
def test_other_manager_cannot_mint(repo):
    executor = make_executor(repo)
    write(repo, "a.txt", "a")
    prepared = _prepared(executor, ("add", {"paths": ["a.txt"]}))
    foreign = GitPermissionManager().grant(prepared, Decision.CONFIRM)
    with pytest.raises(AuthorizationError):
        executor.execute(prepared, foreign)


@pytest.mark.security
def test_expired_authorization(repo):
    now = [1000.0]
    executor = make_executor(repo, ttl=10, clock=lambda: now[0])
    write(repo, "a.txt", "a")
    prepared = _prepared(executor, ("add", {"paths": ["a.txt"]}))
    auth = executor.authorize(prepared, Decision.CONFIRM)
    now[0] += 11
    with pytest.raises(AuthorizationError, match="expirada"):
        executor.execute(prepared, auth)


@pytest.mark.security
def test_authorization_is_single_use(repo):
    executor = make_executor(repo)
    write(repo, "a.txt", "a")
    prepared = _prepared(executor, ("add", {"paths": ["a.txt"]}))
    auth = executor.authorize(prepared, Decision.CONFIRM)
    assert executor.execute(prepared, auth).ok
    with pytest.raises(AuthorizationError, match="utilizada"):
        executor.execute(prepared, auth)


@pytest.mark.security
def test_authorization_for_other_plan_rejected(repo):
    executor = make_executor(repo)
    write(repo, "a.txt", "a")
    write(repo, "b.txt", "b")
    prepared_a = _prepared(executor, ("add", {"paths": ["a.txt"]}))
    prepared_b = _prepared(executor, ("add", {"paths": ["b.txt"]}))
    auth = executor.authorize(prepared_a, Decision.CONFIRM)
    with pytest.raises(AuthorizationError, match="outra operação"):
        executor.execute(prepared_b, auth)


@pytest.mark.security
def test_state_change_after_confirmation_invalidates(repo):
    executor = make_executor(repo)
    write(repo, "a.txt", "a")
    prepared = _prepared(executor, ("add", {"paths": ["a.txt"]}))
    auth = executor.authorize(prepared, Decision.CONFIRM)
    write(repo, "a.txt", "a changed after confirmation, different size")
    with pytest.raises(StateChangedError):
        executor.execute(prepared, auth)


@pytest.mark.security
def test_args_tampered_after_confirmation(repo):
    executor = make_executor(repo)
    write(repo, "a.txt", "a")
    write(repo, ".env", "SECRET=1")
    prepared = _prepared(executor, ("add", {"paths": ["a.txt"]}))
    auth = executor.authorize(prepared, Decision.CONFIRM)
    tampered_plan = executor.build_plan([("add", {"paths": [".env"], "allow_sensitive": True})])
    prepared.plan = tampered_plan  # swap the plan behind the authorization
    with pytest.raises(AuthorizationError):
        executor.execute(prepared, auth)
