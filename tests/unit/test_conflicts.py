import pytest

from grippo.agent.router import route
from grippo.git import conflicts
from grippo.git.conflicts import parse_hunks
from tests.conftest import commit_file, git, make_executor

pytestmark = pytest.mark.git


@pytest.mark.parametrize(
    "message",
    [
        "Como resolvo o conflito?",
        "por que deu conflito?",
        "explique o conflito",
        "me ajude a entender esse conflito",
    ],
)
def test_conflict_questions_route_to_explanation(message):
    assert route(message).action == "explain_conflict"


@pytest.mark.parametrize("message", ["aborte o merge por causa do conflito", "continue o merge"])
def test_conflict_actions_are_not_explanations(message):
    result = route(message)
    assert result is None or result.action != "explain_conflict"


def test_parse_hunks():
    text = "a\n<<<<<<< HEAD\nx\ny\n=======\nz\n>>>>>>> main\nb\n"
    (hunk,) = parse_hunks(text)
    assert hunk.ours == ["x", "y"] and hunk.theirs == ["z"]


def _conflicted(repo):
    commit_file(repo, "a.txt", "base\n", "chore: base")
    git(repo, "switch", "-q", "-c", "feat")
    commit_file(repo, "a.txt", "da feature\n", "feat: muda a")
    git(repo, "switch", "-q", "main")
    commit_file(repo, "a.txt", "da main\n", "fix: muda a na main")
    git(repo, "switch", "-q", "feat")
    git(repo, "merge", "main", check=False)


def test_report_names_both_sides_and_never_resolves(repo):
    _conflicted(repo)
    executor = make_executor(repo)
    report = conflicts.collect(executor.reader, executor.state())
    assert report and report.operation == "merge"
    (item,) = report.files
    assert item.path == "a.txt" and "mesmas linhas" in item.kind
    assert any("feat: muda a" in c for c in item.our_commits)
    assert any("fix: muda a na main" in c for c in item.their_commits)
    text = conflicts.render(report)
    assert "Por que aconteceu" in text and "não escolhe o lado" in text
    assert (repo / "a.txt").read_text().count("<<<<<<<") == 1  # untouched


def test_no_conflict_means_no_report(repo):
    executor = make_executor(repo)
    assert conflicts.collect(executor.reader, executor.state()) is None
