import pytest

from grippo.agent.router import route


def ops(message):
    result = route(message)
    assert result is not None, message
    return result.action, [op for op, _ in result.steps], result


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Quero fazer um commit.", ["commit_changes"]),
        ("faça commit e push na develop", ["commit_changes", "publish"]),
        ("Crie uma branch fix/login para essa correção.", ["branch_create"]),
        ("Mostre o que foi alterado.", ["status", "diff"]),
        ("Faça cherry-pick do commit abc1234", ["cherry_pick"]),
        ("Desfaça o último commit mantendo minhas alterações.", ["undo_last_commit"]),
        ("Faça rebase da minha branch com develop.", ["sync_branch"]),
        ("Quero reverter esse commit 1a2b3c4", ["revert"]),
        ("Verifique se existem alterações pendentes.", ["status", "diff"]),
        ("Envie minhas alterações para o remoto.", ["publish"]),
        ("Mostre o histórico da branch.", ["log"]),
        ("Compare minha branch com main.", ["compare", "diff"]),
        ("Atualize minha branch com develop", ["sync_branch"]),
        ("mude para a branch develop", ["switch"]),
        ("volte para a main", ["switch"]),
        ("volte pra branch develop", ["switch"]),
        ("vá para a branch feature/login", ["switch"]),
        ("liste as branches", ["branch_list"]),
        ("git stash", ["stash_push"]),
        ("recupere o stash", ["stash_pop"]),
        ("faça pull", ["pull"]),
        ("git fetch", ["fetch"]),
        ("mostre o reflog", ["reflog"]),
        ("crie a tag v1.0.0", ["tag_create"]),
        ("descarte as alterações do arquivo src/app.py", ["discard_changes"]),
        ("git clean", ["clean"]),
        ("reset --hard HEAD~2", ["reset"]),
        ("delete a branch feature/old", ["branch_delete"]),
    ],
)
def test_routes(message, expected):
    action, steps, _ = ops(message)
    assert action == "execute" and steps == expected


def test_extracts_arguments():
    _, _, r = ops("Crie uma branch fix/login para essa correção.")
    assert r.steps[0][1]["name"] == "fix/login"
    _, _, r = ops("reset --hard HEAD~2")
    assert r.steps[0][1] == {"mode": "hard", "target": "HEAD~2"}
    _, _, r = ops("Desfaça o último commit mantendo minhas alterações.")
    assert r.steps[0][1]["keep_changes"] is True
    _, _, r = ops("Quero desfazer as alterações desse commit 1a2b3c4 sem apagar o histórico.")
    assert r.steps == [("revert", {"commit": "1a2b3c4"})]
    _, _, r = ops("faça rebase da minha branch com develop")
    assert r.steps[0][1] == {"base": "develop", "strategy": "rebase"}
    _, _, r = ops("delete a branch feature/old")
    assert r.steps[0][1]["force"] is False


def test_force_words_are_explicit():
    _, steps, r = ops("faça push --force")
    assert steps == ["push"] and r.steps[0][1] == {"force": True}
    _, _, r = ops("envie minhas alterações")
    assert r.steps[0] == ("publish", {})


def test_not_git_requests_are_refused():
    result = route("implemente a tela de cadastro")
    assert result is not None and result.action == "answer"
    result = route("corrija o bug do login")
    assert result is not None and result.action == "answer"


def test_missing_info_asks():
    assert route("crie uma branch").action == "clarify"
    assert route("faça cherry-pick").action == "clarify"


def test_unknown_returns_none():
    assert route("qual a previsão do tempo?") is None


def test_conflict_markers_detected(tmp_path):
    from grippo.agent.router import _files_with_conflict_markers

    (tmp_path / "bad.txt").write_text("a\n<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> main\n")
    (tmp_path / "doc.md").write_text("exemplo:\n<<<<<<< aqui\nsó um marcador solto\n")
    (tmp_path / "ok.txt").write_text("resolvido\n")
    found = _files_with_conflict_markers(str(tmp_path), ["bad.txt", "doc.md", "ok.txt", "gone.txt"])
    assert found == ["bad.txt"]  # a file deleted to resolve a modify/delete conflict is fine


@pytest.mark.parametrize(
    "message",
    ["resolvi os conflitos, faça o commit", "como continuo o merge depois do conflito"],
)
def test_statements_about_conflicts_are_not_questions(message):
    result = route(message)
    assert result is None or result.action != "explain_conflict"
