from grippo.llm.commit import CommitDrafter, heuristic_message, normalize_message
from tests.conftest import FakeOllama, commit_file, write


def test_normalize_message():
    assert normalize_message("feat(form): add signup form") == "feat(form): add signup form"
    assert normalize_message("`fix: handle null`") == "fix: handle null"
    assert normalize_message("added stuff") is None
    assert len(normalize_message("feat: " + "x" * 200) or "") <= 72


def test_heuristic_selection_keeps_unrelated_out(repo, executor):
    commit_file(repo, "src/form/signup.js", "a", "feat: base")
    write(repo, "src/form/signup.js", "b")
    write(repo, "src/form/validation.js", "new")
    write(repo, "src/billing/invoice.js", "unrelated")
    write(repo, ".env", "SECRET=1")
    state = executor.state()
    drafter = CommitDrafter(None)
    draft = drafter.draft("alterações do form", state, executor.reader,
                          state.unstaged + list(state.untracked))
    assert ".env" in draft.excluded_sensitive and ".env" not in draft.files
    assert all("form" in f for f in draft.files)
    assert any("billing" in f for f in draft.unrelated)
    assert draft.message.startswith(("feat", "chore"))


def test_heuristic_default_excludes_untracked(repo, executor):
    write(repo, "README.md", "changed")
    write(repo, "notes.txt", "scratch")
    state = executor.state()
    draft = CommitDrafter(None).draft("", state, executor.reader,
                                      state.unstaged + list(state.untracked))
    assert draft.files == ["README.md"] and draft.unrelated == ["notes.txt"]
    assert heuristic_message(["README.md"], state).startswith("docs")


def test_llm_per_file_relevance_and_bad_message(repo, executor, fake_ollama: FakeOllama):
    write(repo, "README.md", "changed")
    write(repo, "form.html", "<form>")
    # form.html matches "formulário" deterministically; only README.md goes to the model
    fake_ollama.replies.extend([{"related": False}, {"message": "I changed some things"}])
    state = executor.state()
    draft = CommitDrafter(fake_ollama.client()).draft(
        "formulário", state, executor.reader, state.unstaged + list(state.untracked))
    assert draft.files == ["form.html"] and draft.unrelated == ["README.md"]
    assert normalize_message(draft.message) == draft.message
    assert any("inválida" in n for n in draft.notes) and draft.source == "heuristic"
    assert len(fake_ollama.calls) == 2


def test_keyword_match_skips_llm_and_pulls_stem_siblings(repo, executor, fake_ollama):
    write(repo, "src/signup_form.html", "<form>")
    write(repo, "src/signup_form.js", "js")
    write(repo, "src/other.js", "x")
    fake_ollama.replies.extend([{"related": False}, {"message": "feat(signup): add signup form"}])
    state = executor.state()
    draft = CommitDrafter(fake_ollama.client()).draft("commit do signup", state,
                                                      executor.reader, list(state.untracked))
    assert draft.files == ["src/signup_form.html", "src/signup_form.js"]
    assert draft.message == "feat(signup): add signup form" and draft.source == "llm"


def test_generic_request_needs_no_selection_call(repo, executor, fake_ollama):
    write(repo, "README.md", "changed")
    write(repo, "notes.txt", "scratch")
    fake_ollama.replies.append({"message": "docs: update readme"})
    state = executor.state()
    draft = CommitDrafter(fake_ollama.client()).draft("faça commit", state, executor.reader,
                                                      state.unstaged + list(state.untracked))
    assert draft.files == ["README.md"] and len(fake_ollama.calls) == 1
    everything = CommitDrafter(None).draft("commita tudo", state, executor.reader,
                                           state.unstaged + list(state.untracked))
    assert everything.files == ["README.md", "notes.txt"]


def test_portuguese_request_matches_english_paths(repo, executor):
    write(repo, "src/forms/cadastro_form.html", "<form>")
    write(repo, "src/billing/invoice.py", "x")
    write(repo, "src/auth/login.py", "y")
    state = executor.state()
    drafter = CommitDrafter(None)
    for request, expected in (("alterações do formulário", ["src/forms/cadastro_form.html"]),
                              ("correção da fatura", ["src/billing/invoice.py"]),
                              ("tela de autenticação", ["src/auth/login.py"])):
        draft = drafter.draft(request, state, executor.reader, list(state.untracked))
        assert draft.files == expected, (request, draft.files)


def test_long_message_cut_at_word_boundary():
    msg = normalize_message("feat(core): add functionality to resolve remote revisions and warn about stale")
    assert msg is not None and len(msg) <= 72 and not msg.endswith(" ab")
    assert msg.endswith("warn")
