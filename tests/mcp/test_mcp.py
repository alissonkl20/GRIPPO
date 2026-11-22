import io
import json

import pytest

from grippo.config import load_config
from grippo.engine.audit import AuditLog
from grippo.engine.executor import GitExecutor
from grippo.engine.permissions import GitPermissionManager
from grippo.git.lock import RepoLocks
from grippo.git.repo import discover
from grippo.mcp.proposals import ProposalStore, Status
from grippo.mcp.server import McpServer
from grippo.mcp.tools import TOOL_DEFS, GrippoTools
from grippo.paths import get_paths
from grippo.runtime import Runtime
from tests.conftest import commit_file, git, write

pytestmark = pytest.mark.git


@pytest.fixture
def tools(repo, tmp_path):
    paths = get_paths()
    paths.ensure()
    repository = discover(repo)
    executor = GitExecutor(
        repository,
        permissions=GitPermissionManager(),
        audit=AuditLog(None),
        locks=RepoLocks(tmp_path / "locks", timeout=5),
    )
    runtime = Runtime(load_config(paths.config_file), paths, repository, executor, None, None)  # type: ignore[arg-type]
    return GrippoTools(runtime, ProposalStore(tmp_path / "proposals"))


def propose(tools, steps, request="", reason=""):
    return tools.call("grippo_propose", {"steps": steps, "user_request": request, "reason": reason})


def test_no_tool_can_approve_or_execute_changes():
    names = {tool["name"] for tool in TOOL_DEFS}
    assert not {n for n in names if "approve" in n or "execute" in n or "run" in n}


def test_read_only_proposal_runs_immediately(tools):
    reply = propose(tools, [{"op": "log", "args": {"n": 3}}])
    assert not reply.is_error and "init" in reply.text


def test_mutation_becomes_proposal_and_changes_nothing(tools, repo):
    write(repo, "a.txt", "x")
    reply = propose(
        tools,
        [
            {"op": "add", "args": {"paths": ["a.txt"]}},
            {"op": "commit", "args": {"message": "feat: a"}},
        ],
        "faça commit de a.txt",
    )
    assert (
        not reply.is_error
        and "NADA foi executado" in reply.text
        and "grippo approve p-" in reply.text
    )
    assert git(repo, "status", "--short").stdout.startswith("??")  # still untracked
    (proposal,) = tools.store.list(only_pending=True)
    assert proposal.status == Status.PENDING and proposal.risk == "moderado"


def test_mutation_requires_user_request(tools, repo):
    write(repo, "a.txt", "x")
    reply = propose(tools, [{"op": "add", "args": {"paths": ["a.txt"]}}])
    assert reply.is_error and "user_request" in reply.text
    assert tools.store.list() == []


def test_hallucinated_target_is_refused(tools, repo):
    git(repo, "branch", "feature/old")
    reply = propose(
        tools, [{"op": "branch_delete", "args": {"name": "feature/old"}}], "limpe o repositório"
    )
    assert reply.is_error and "anti-alucinação" in reply.text
    assert tools.store.list() == []


def test_noop_and_invalid_plans_are_rejected(tools):
    noop = propose(tools, [{"op": "rebase", "args": {"upstream": "main"}}], "rebase com main")
    assert noop.is_error and "nada a fazer" in noop.text
    unknown = tools.call(
        "grippo_propose", {"steps": [{"op": "rm_rf", "args": {}}], "user_request": "x"}
    )
    assert unknown.is_error
    assert propose(tools, []).is_error


def test_unknown_args_and_shell_injection_are_rejected(tools):
    reply = propose(
        tools,
        [{"op": "branch_create", "args": {"name": "x; rm -rf ~"}}],
        "crie a branch x; rm -rf ~",
    )
    assert reply.is_error


def test_proposal_status_flow(tools, repo):
    commit_file(repo, "a.txt", "a", "feat: a")
    propose(tools, [{"op": "revert", "args": {"commit": "HEAD"}}], "reverta o último commit")
    (proposal,) = tools.store.list(only_pending=True)
    assert proposal.id in tools.call("grippo_proposal_status", {}).text
    assert "pending" in tools.call("grippo_proposal_status", {"id": proposal.id}).text
    assert tools.call("grippo_proposal_status", {"id": "p-nope"}).is_error


def test_proposals_expire(tmp_path):
    now = [1000.0]
    store = ProposalStore(tmp_path, clock=lambda: now[0])
    proposal = store.create(
        repo="/r", steps=[], user_request="x", reason="", risk="baixo", binding="b", ttl_sec=60
    )
    now[0] += 61
    assert store.get(proposal.id).status == "expired"


# ---------------------------------------------------------------------- protocol
def rpc(server, method, params=None, id_=1):
    message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if id_ is not None:
        message["id"] = id_
    return server.handle(message)


def test_protocol_handshake_and_listing(tools):
    server = McpServer(tools)
    init = rpc(server, "initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "t"}})
    assert init["result"]["serverInfo"]["name"] == "grippo"
    assert rpc(server, "notifications/initialized", id_=None) is None
    listing = rpc(server, "tools/list")["result"]["tools"]
    assert {t["name"] for t in listing} >= {"grippo_propose", "grippo_repo_state"}
    assert rpc(server, "nao/existe")["error"]["code"] == -32601


def test_tools_call_over_stdio(tools):
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "grippo_repo_state", "arguments": {}},
        }
    )
    out = io.StringIO()
    McpServer(tools).serve(io.StringIO(request + "\nnot json\n"), out)
    first, second = (json.loads(line) for line in out.getvalue().splitlines())
    assert first["id"] == 7 and "branch: main" in first["result"]["content"][0]["text"]
    assert second["error"]["code"] == -32700


def test_even_low_risk_changes_are_only_proposals(tools, repo):
    write(repo, "a.txt", "x")
    reply = propose(tools, [{"op": "add", "args": {"paths": ["a.txt"]}}], "adicione a.txt")
    assert "NADA foi executado" in reply.text
    assert git(repo, "status", "--short").stdout.startswith("??")


def test_printed_config_puts_repo_before_the_subcommand(repo):
    from grippo.cli.main import _parser
    from grippo.mcp.setup import render_config

    config = render_config(repo)
    args = _parser().parse_args(["--repo", str(repo), "mcp"])  # what the config will run
    assert args.repo == repo and f"--repo {repo.resolve()} mcp" in config
