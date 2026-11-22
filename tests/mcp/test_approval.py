import sys

import pytest

from grippo.mcp import approval
from grippo.mcp.proposals import ProposalStore
from grippo.paths import get_paths
from tests.conftest import git, head, write

pytestmark = pytest.mark.git


def _proposal(repo, steps, request):
    paths = get_paths()
    paths.ensure()
    return ProposalStore(paths.proposals_dir).create(
        repo=str(repo),
        steps=steps,
        user_request=request,
        reason="teste",
        risk="moderado",
        binding="stale",
        ttl_sec=600,
        client="pytest",
    )


def _tty(monkeypatch, *answers):
    replies = iter(answers)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(approval, "make_reader", lambda _: lambda prompt: next(replies))


def _commit_steps():
    return [
        {"op": "add", "args": {"paths": ["a.txt"], "allow_sensitive": False}},
        {"op": "commit", "args": {"message": "feat: a", "amend": False, "allow_empty": False}},
    ]


def test_approve_requires_a_terminal(repo, capsys):
    proposal = _proposal(repo, _commit_steps(), "commit a.txt")
    assert approval.run_approve(proposal.id) == 2
    assert "TTY" in capsys.readouterr().err
    assert ProposalStore(get_paths().proposals_dir).get(proposal.id).pending


def test_human_approval_executes_and_records(repo, monkeypatch):
    write(repo, "a.txt", "x")
    proposal = _proposal(repo, _commit_steps(), "commit a.txt")
    before = head(repo)
    _tty(monkeypatch, "c")
    assert approval.run_approve(proposal.id) == 0
    assert (
        head(repo) != before and git(repo, "log", "-1", "--format=%s").stdout.strip() == "feat: a"
    )
    stored = ProposalStore(get_paths().proposals_dir).get(proposal.id)
    assert stored.status == "done"


def test_human_refusal_changes_nothing(repo, monkeypatch):
    write(repo, "a.txt", "x")
    proposal = _proposal(repo, _commit_steps(), "commit a.txt")
    before = head(repo)
    _tty(monkeypatch, "n")
    assert approval.run_approve(proposal.id) == 3
    assert head(repo) == before
    assert ProposalStore(get_paths().proposals_dir).get(proposal.id).status == "cancelled"


def test_tampered_proposal_is_refused_by_guard(repo, monkeypatch):
    git(repo, "branch", "feature/x")
    proposal = _proposal(
        repo, [{"op": "branch_delete", "args": {"name": "feature/x", "force": False}}], "oi"
    )
    _tty(monkeypatch, "confirmar")
    assert approval.run_approve(proposal.id) == 1
    assert git(repo, "branch", "--list", "feature/x").stdout.strip()
