"""End-to-end Git workflows through the Assistant, deterministic path (no LLM).

Each test builds a real repository (and a bare remote where needed), asks for an action in
natural language, checks what GRIPPO proposes, confirms, and verifies the resulting Git
state. These are the scenarios a user actually runs: commit everything, push, merge into
a named branch, create/resolve/abort a conflict, revert, rebase, and the refusal guards.
"""

import pytest

from grippo.agent.assistant import Assistant
from grippo.agent.planner import Planner
from grippo.agent.session import Session
from grippo.engine.permissions import Decision
from grippo.llm.commit import CommitDrafter
from grippo.llm.interpreter import Interpreter
from tests.conftest import commit_file, git, head, make_executor, subject, write

pytestmark = pytest.mark.git


def assistant_for(repo):
    executor = make_executor(repo)
    return Assistant(executor, Interpreter(None), Planner(executor, CommitDrafter(None)), Session())


def confirm(assistant, turn):
    assert turn.kind == "confirm", (turn.kind, turn.message)
    result = assistant.decide(turn.prepared, Decision.CONFIRM)
    assert result is not None
    return result


def branch(repo):
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def clean(repo):
    return git(repo, "status", "--porcelain").stdout.strip() == ""


def tracked_files(repo, rev="HEAD"):
    return set(git(repo, "ls-tree", "-r", "--name-only", rev).stdout.split())


def on_branch(repo, name, *, create=False):
    git(repo, "checkout", "-q", *(["-b"] if create else []), name)


# ----------------------------------------------------------------- commit / push


def test_commit_all_takes_staged_unstaged_and_untracked(repo):
    """'commit de todas as alterações' must not leave anything behind, even when some
    files were already staged before the request."""
    write(repo, "a.txt", "a")
    write(repo, "b.txt", "b")
    write(repo, "README.md", "changed\n")
    git(repo, "add", "--", "a.txt")  # staged earlier
    assistant = assistant_for(repo)
    turn = assistant.handle("faça commit de todas alterações")
    confirm(assistant, turn)
    assert clean(repo)
    assert {"a.txt", "b.txt", "README.md"} <= tracked_files(repo)


def test_commit_and_push_everything(remote_repo):
    repo, remote = remote_repo
    write(repo, "feature.txt", "f")
    write(repo, "README.md", "more\n")
    assistant = assistant_for(repo)
    turn = assistant.handle("faça commit de todas alterações e push")
    ops = [s.op for s in turn.prepared.plan.steps]
    assert ops == ["add", "commit", "push"], ops
    confirm(assistant, turn)
    assert clean(repo)
    assert head(repo) == git(remote, "rev-parse", "main").stdout.strip()


def test_commit_refuses_other_branch(repo):
    on_branch(repo, "develop", create=True)
    on_branch(repo, "main")
    write(repo, "x.txt", "x")
    before = head(repo)
    assistant = assistant_for(repo)
    turn = assistant.handle("faça commit de tudo na develop")
    assert turn.kind != "confirm"
    assert head(repo) == before
    assert branch(repo) == "main"


# ------------------------------------------------------------------ diff / read


def test_diff_is_read_only_and_shows_changes(repo):
    write(repo, "README.md", "hello changed\n")
    assistant = assistant_for(repo)
    turn = assistant.handle("mostre o diff")
    assert turn.kind == "result", turn.kind
    assert "hello changed" in "".join(r.stdout for r in turn.result.results)
    assert not (repo / ".git" / "index.lock").exists()
    assert git(repo, "status", "--porcelain").stdout.strip() == "M README.md"


# ------------------------------------------------------------------------ merge


def _feature_on_develop(repo):
    on_branch(repo, "develop", create=True)
    commit_file(repo, "feature.txt", "feature", "feat: feature")
    on_branch(repo, "main")


def test_merge_named_branch_into_named_target(repo):
    """'faça merge da develop na main' while on develop: must end on main with develop's
    commit, not merge develop into itself."""
    _feature_on_develop(repo)
    on_branch(repo, "develop")
    assistant = assistant_for(repo)
    turn = assistant.handle("faça merge da develop na main")
    confirm(assistant, turn)
    assert branch(repo) == "main"
    assert "feature.txt" in tracked_files(repo)


def test_merge_into_current_branch(repo):
    _feature_on_develop(repo)
    assistant = assistant_for(repo)
    turn = assistant.handle("faça merge da develop")
    confirm(assistant, turn)
    assert "feature.txt" in tracked_files(repo)


def test_merge_branch_into_itself_is_refused(repo):
    assistant = assistant_for(repo)
    before = head(repo)
    turn = assistant.handle("faça merge da main")
    assert turn.kind != "confirm"
    assert head(repo) == before


# --------------------------------------------------------------------- conflict


def _conflicting_branches(repo):
    on_branch(repo, "develop", create=True)
    commit_file(repo, "README.md", "from develop\n", "feat: develop text")
    on_branch(repo, "main")
    commit_file(repo, "README.md", "from main\n", "fix: main text")


def test_conflict_is_detected_explained_and_aborted(repo):
    _conflicting_branches(repo)
    assistant = assistant_for(repo)
    turn = assistant.handle("faça merge da develop")
    result = confirm(assistant, turn)
    assert result.final_state is not None and "README.md" in result.final_state.conflicted
    assert (repo / ".git" / "MERGE_HEAD").exists()

    explain = assistant.handle("por que deu conflito?")
    assert explain.kind == "answer"

    abort = assistant.handle("aborte o merge")
    confirm(assistant, abort)
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert clean(repo)
    assert (repo / "README.md").read_text() == "from main\n"


def test_conflict_resolved_then_continue(repo):
    _conflicting_branches(repo)
    assistant = assistant_for(repo)
    confirm(assistant, assistant.handle("faça merge da develop"))
    write(repo, "README.md", "resolved\n")
    turn = assistant.handle("continue o merge")
    confirm(assistant, turn)
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert len(git(repo, "rev-list", "--parents", "-n1", "HEAD").stdout.split()) == 3
    assert (repo / "README.md").read_text() == "resolved\n"


def test_continue_refused_while_markers_remain(repo):
    _conflicting_branches(repo)
    assistant = assistant_for(repo)
    confirm(assistant, assistant.handle("faça merge da develop"))
    turn = assistant.handle("continue o merge")
    assert turn.kind == "clarify", turn.kind
    assert (repo / ".git" / "MERGE_HEAD").exists()


# ----------------------------------------------------------------- revert/undo


def test_revert_last_commit_keeps_history(repo):
    commit_file(repo, "work.txt", "w", "feat: work")
    count = int(git(repo, "rev-list", "--count", "HEAD").stdout)
    assistant = assistant_for(repo)
    confirm(assistant, assistant.handle("reverta o último commit"))
    assert "work.txt" not in tracked_files(repo)
    assert int(git(repo, "rev-list", "--count", "HEAD").stdout) == count + 1


def test_undo_last_commit_keeps_changes(repo):
    commit_file(repo, "work.txt", "w", "feat: work")
    assistant = assistant_for(repo)
    confirm(assistant, assistant.handle("desfaça o último commit mas mantenha as alterações"))
    assert subject(repo) == "chore: init"
    assert "work.txt" in git(repo, "diff", "--cached", "--name-only").stdout


def test_undo_last_commit_does_not_lose_work_without_asking(repo):
    commit_file(repo, "work.txt", "w", "feat: work")
    assistant = assistant_for(repo)
    turn = assistant.handle("desfaça o último commit")
    if turn.kind == "confirm":
        assert turn.prepared.needs_confirmation
    assert subject(repo) == "feat: work"  # nothing done before the user decides


# --------------------------------------------------------------------- rebase


def test_rebase_feature_onto_develop(repo):
    _feature_on_develop(repo)
    on_branch(repo, "feature/x", create=True)
    commit_file(repo, "x.txt", "x", "feat: x")
    assistant = assistant_for(repo)
    confirm(assistant, assistant.handle("faça rebase na develop"))
    assert "feature.txt" in tracked_files(repo)
    assert subject(repo) == "feat: x"


# ---------------------------------------------------------------------- guards


def test_cannot_delete_protected_branch(repo):
    on_branch(repo, "develop", create=True)
    on_branch(repo, "main")
    assistant = assistant_for(repo)
    turn = assistant.handle("exclua a branch develop")
    assert turn.kind != "result"
    if turn.kind == "confirm":
        assistant.decide(turn.prepared, Decision.CONFIRM)
    assert "develop" in git(repo, "branch", "--list").stdout


def test_cannot_delete_current_branch(repo):
    assistant = assistant_for(repo)
    turn = assistant.handle("exclua a branch main")
    assert turn.kind != "result"
    assert "main" in git(repo, "branch", "--list").stdout


def test_hard_reset_requires_confirmation_and_keeps_work_until_then(repo):
    write(repo, "README.md", "precious\n")
    assistant = assistant_for(repo)
    # Without an explicit target the grounding guard refuses (kind "denied"); with one it
    # proposes the reset and waits for confirmation.
    assert assistant.handle("faça reset hard").kind == "denied"
    turn = assistant.handle("faça reset --hard HEAD")
    assert turn.kind == "confirm" and turn.prepared.needs_confirmation
    assert (repo / "README.md").read_text() == "precious\n"


def test_force_push_is_never_implicit(remote_repo):
    repo, _ = remote_repo
    assistant = assistant_for(repo)
    turn = assistant.handle("faça push")
    assert (
        all(s.args.get("force") is not True for s in turn.prepared.plan.steps)
        if (turn.kind == "confirm")
        else True
    )


# --------------------------------------------------------------------- PR query


def test_pull_request_question_never_runs_pull(repo, monkeypatch):
    from grippo.git import github

    calls = []
    monkeypatch.setattr(
        github,
        "list_pull_requests",
        lambda root, base=None, state="open": (
            calls.append(base)
            or [
                {
                    "number": 7,
                    "title": "Develop",
                    "head": "develop",
                    "base": "main",
                    "author": "alissonkl20",
                    "url": "",
                    "draft": False,
                }
            ]
        ),
    )
    assistant = assistant_for(repo)
    before = head(repo)
    turn = assistant.handle("tem alguma solicitação de pull request em develop")
    assert turn.kind == "answer", turn.kind
    assert "#7" in turn.message
    assert calls == ["develop"]
    assert head(repo) == before


# ------------------------------------------- regressions seen in the real CLI


def test_unstage_resolves_file_name_without_extension(repo):
    write(repo, "README.md", "changed\n")
    git(repo, "add", "README.md")
    assistant = assistant_for(repo)
    turn = assistant.handle("quero desfazer o git add do README")
    assert turn.prepared.plan.steps[0].args["paths"] == ["README.md"]
    confirm(assistant, turn)
    assert git(repo, "diff", "--cached", "--name-only").stdout.strip() == ""
    assert (repo / "README.md").read_text() == "changed\n"  # content kept


def test_save_everything_in_a_commit_keeps_untracked(repo):
    write(repo, "README.md", "changed\n")
    write(repo, "debug.txt", "d")
    write(repo, "y.js", "y")
    assistant = assistant_for(repo)
    confirm(assistant, assistant.handle("salva tudo num commit"))
    assert clean(repo)


def test_go_back_to_commit_asks_instead_of_resetting(repo):
    sha = commit_file(repo, "a.txt", "a", "feat: a")
    commit_file(repo, "b.txt", "b", "feat: b")
    before = head(repo)
    turn = assistant_for(repo).handle(f"volta tudo pro commit {sha[:7]}")
    assert turn.kind == "clarify" and "reset --hard" in turn.message
    assert head(repo) == before


def test_unpushed_commits_are_listed_read_only(remote_repo):
    repo, _ = remote_repo
    commit_file(repo, "a.txt", "a", "feat: not pushed yet")
    turn = assistant_for(repo).handle("quais commits eu ainda não enviei")
    assert turn.kind == "result", (turn.kind, turn.message)
    assert "feat: not pushed yet" in "".join(r.stdout for r in turn.result.results)


def test_merge_preview_after_switch_does_not_claim_nothing_to_merge(repo):
    _feature_on_develop(repo)
    on_branch(repo, "develop")
    turn = assistant_for(repo).handle("faça merge da develop na main")
    merge = turn.prepared.previews[1]
    assert not any("nada a integrar" in w for w in merge.impact.warnings)
    assert "develop em develop" not in " ".join(merge.impact.consequences)


def test_commit_preview_counts_files_of_the_add_step(repo):
    write(repo, "a.txt", "a")
    write(repo, "b.txt", "b")
    git(repo, "add", "a.txt")
    turn = assistant_for(repo).handle("faça commit de todas alterações")
    commit = turn.prepared.previews[1]
    assert not commit.impact.warnings, commit.impact.warnings
    assert "passo anterior" in commit.impact.consequences[0]


def test_rebase_uses_local_base_when_it_has_unpushed_commits(remote_repo):
    repo, _ = remote_repo
    on_branch(repo, "develop", create=True)
    git(repo, "push", "-q", "-u", "origin", "develop")
    commit_file(repo, "local.txt", "l", "feat: only on local develop")  # not pushed
    on_branch(repo, "main")
    on_branch(repo, "feature/x", create=True)
    commit_file(repo, "x.txt", "x", "feat: x")
    assistant = assistant_for(repo)
    turn = assistant.handle("faça rebase na develop")
    assert turn.prepared.plan.steps[-1].args["upstream"] == "develop"
    confirm(assistant, turn)
    assert "local.txt" in tracked_files(repo)
