"""Intent corpus: natural-language requests and the Git operation each one must produce.

Shared by the deterministic test (tests/unit) and the real-model evaluation
(tests/llm_real). Each case:

* ``text``      what the user says (pt-BR or en)
* ``ops``       ordered list; each item is a set of acceptable operation names
* ``args``      values that must appear in some step's args (compared as strings)
* ``forbidden`` operations that must never be proposed for this request
* ``action``    for requests that are not Git operations: expected non-execute action
"""

from __future__ import annotations

from typing import Any

CASES: list[dict[str, Any]] = [
    # commit / push
    {
        "text": "faça commit de todas alterações e push",
        "ops": [{"commit_changes"}, {"publish", "push"}],
    },
    {"text": "commit tudo", "ops": [{"commit_changes"}], "forbidden": {"reset", "clean"}},
    {"text": "Commit everything and push it", "ops": [{"commit_changes"}, {"publish", "push"}]},
    {
        "text": "envie meus commits para o remoto",
        "ops": [{"publish", "push"}],
        "forbidden": {"push_delete"},
    },
    {"text": "faça push forçado para origin", "ops": [{"push"}], "args": {"force": "True"}},
    # diff / status / log / show
    {"text": "mostre o diff", "ops": [{"diff"}], "forbidden": {"commit_changes", "commit"}},
    {"text": "Please show me the diff of the working tree.", "ops": [{"diff"}]},
    {"text": "mostre o status", "ops": [{"status", "diff"}]},
    {"text": "quais arquivos mudaram", "ops": [{"status", "diff"}]},
    {"text": "qual o histórico de commits?", "ops": [{"log"}]},
    {"text": "mostre o último commit", "ops": [{"show", "log"}]},
    {"text": "qual a diferença entre main e develop", "ops": [{"compare", "diff", "log"}]},
    # merge / sync
    {
        "text": "faça merge da develop na main",
        "ops": [{"switch", "checkout"}, {"merge"}],
        "args": {"rev": "develop", "branch": "main"},
    },
    {
        "text": "faça merge da develop",
        "ops": [{"merge"}],
        "args": {"rev": "develop"},
        "forbidden": {"reset"},
    },
    {"text": "atualize minha branch com a develop", "ops": [{"sync_branch", "merge", "rebase"}]},
    {"text": "faça rebase na develop", "ops": [{"sync_branch", "rebase"}]},
    {"text": "aborte o merge", "ops": [{"merge_abort"}]},
    {"text": "continue o merge", "ops": [{"merge_continue", "add"}]},
    {"text": "continue o rebase", "ops": [{"rebase_continue"}]},
    # revert / undo / reset
    {"text": "reverta o último commit", "ops": [{"revert"}], "forbidden": {"reset"}},
    {
        "text": "desfaça o último commit mas mantenha as alterações",
        "ops": [{"undo_last_commit", "reset"}],
        "forbidden": {"clean"},
    },
    {"text": "faça cherry-pick do abc1234", "ops": [{"cherry_pick"}]},
    {"text": "desfaça o add do arquivo x.txt", "ops": [{"unstage", "restore"}]},
    # branches / switch
    {
        "text": "crie uma branch chamada fix/login",
        "ops": [{"branch_create"}],
        "args": {"name": "fix/login"},
    },
    {"text": "mude para a develop", "ops": [{"switch", "checkout"}], "args": {"branch": "develop"}},
    {
        "text": "Please switch to the develop branch.",
        "ops": [{"switch", "checkout"}],
        "args": {"branch": "develop"},
    },
    {
        "text": "exclua a branch feature/antiga",
        "ops": [{"branch_delete"}],
        "args": {"name": "feature/antiga"},
    },
    {"text": "liste as branches", "ops": [{"branch_list"}]},
    {"text": "qual é a branch atual?", "ops": [{"status", "branch_list"}]},
    # stash / pull / fetch / remotes / tags
    {"text": "guarde minhas alterações temporariamente", "ops": [{"stash_push"}]},
    {"text": "recupere o stash", "ops": [{"stash_pop", "stash_apply"}]},
    {"text": "faça pull", "ops": [{"pull"}]},
    {"text": "faça fetch", "ops": [{"fetch"}]},
    {"text": "adicione o remoto origin https://github.com/x/y.git", "ops": [{"remote_add"}]},
    {
        "text": "I want to tag this commit as v1.2.0.",
        "ops": [{"tag_create"}],
        "args": {"name": "v1.2.0"},
    },
    # GitHub pull requests (never a pull)
    {
        "text": "tem alguma solicitação de pull request em develop?",
        "ops": [],
        "forbidden": {"pull", "fetch", "merge", "push"},
        "pr": True,
    },
    {
        "text": "quais pull requests estão abertas?",
        "ops": [],
        "forbidden": {"pull", "fetch"},
        "pr": True,
    },
    {
        "text": "crie um pull request da develop para a main",
        "ops": [],
        "forbidden": {"pull", "push", "merge"},
        "action": {"answer", "clarify"},
    },
    # guards: requests that must not be executed as written
    {
        "text": "apague tudo do repositório",
        "ops": [],
        "forbidden": {"clean", "rm", "discard_changes", "branch_delete"},
        "action": {"answer", "clarify"},
    },
    {"text": "exclua a branch main", "ops": [{"branch_delete"}], "forbidden": {"push_delete"}},
    # not git: must be answered, not executed
    {"text": "implemente o login com google", "ops": [], "action": {"answer", "clarify"}},
    {"text": "corrija o bug no formulário de cadastro", "ops": [], "action": {"answer", "clarify"}},
]


def check_case(
    case: dict[str, Any], action: str, steps: list[tuple[str, dict[str, Any]]]
) -> list[str]:
    """Problems with the proposal for ``case``; an empty list means it is correct."""
    problems: list[str] = []
    ops = [op for op, _ in steps]
    for forbidden in sorted(case.get("forbidden", set()) & set(ops)):
        problems.append(f"propôs operação proibida: {forbidden}")
    position = 0
    for wanted in case["ops"]:
        while position < len(ops) and ops[position] not in wanted:
            position += 1
        if position >= len(ops):
            problems.append(f"faltou uma de {sorted(wanted)} (propôs {ops or action})")
            break
        position += 1
    for key, value in case.get("args", {}).items():
        found = set()
        for _, args in steps:
            value_found = args.get(key)
            if isinstance(value_found, list):
                found.update(str(item) for item in value_found)
            elif key in args:
                found.add(str(value_found))
        if str(value) not in found:
            problems.append(f"argumento {key}={value!r} ausente (encontrado {sorted(found)})")
    refused = case.get("action") is not None and action not in case["action"]
    if not case["ops"] and refused and ops and not set(ops) <= {"pr_list"}:
        problems.append(f"deveria responder/perguntar, mas propôs {ops}")
    return problems


# Phrasings that started as holdout and were then used to fix rules (2026-11-23). They are
# tuning data now, not an honest estimate.
CASES += [
    {"text": "sobe as mudanças pro remoto", "ops": [{"publish", "push"}]},
    {
        "text": "junta a branch feature/pay na develop",
        "ops": [{"checkout", "switch"}, {"merge"}],
        "args": {"rev": "feature/pay"},
    },
    {"text": "esconde minhas mudanças", "ops": [{"stash_push"}]},
    {"text": "quais commits eu ainda não enviei", "ops": [{"status", "log"}]},
    {
        "text": "rebase minha feature em cima da main",
        "ops": [{"rebase", "sync_branch"}],
        "args": {"base": "main"},
    },
    {
        "text": "cria uma tag v2.0.0 nesse commit",
        "ops": [{"tag_create"}],
        "args": {"name": "v2.0.0"},
    },
    # "README" → README.md needs the staged files (state): see tests/e2e/test_git_workflows.py
    {"text": "quero desfazer o git add do README", "ops": [{"unstage", "restore"}]},
    {
        "text": "volta tudo pro commit abc1234",
        "ops": [],
        "action": {"clarify"},
        "forbidden": {"commit_changes", "reset"},
    },
]

# Earlier holdout phrasings that were never used to fix a rule.
CASES_EARLIER_HOLDOUT: list[dict[str, Any]] = [
    {"text": "manda tudo que eu fiz pro github", "ops": [{"publish", "push"}]},
    {"text": "salva tudo num commit chamado feat: tela de login", "ops": [{"commit_changes"}]},
    {"text": "quero ver o que eu mudei nos arquivos", "ops": [{"diff", "status"}]},
    {"text": "me mostra as diferenças antes de commitar", "ops": [{"diff"}]},
    {"text": "traz as alterações da main pra minha branch", "ops": [{"merge", "sync_branch"}]},
    {"text": "cancela o merge que deu errado", "ops": [{"merge_abort"}]},
    {
        "text": "me explica por que o merge deu conflito",
        "ops": [],
        "action": {"answer", "clarify"},
        "forbidden": {"merge_continue", "merge_abort", "reset"},
    },
    {
        "text": "cria a branch hotfix/x a partir da main",
        "ops": [{"branch_create"}],
        "args": {"name": "hotfix/x"},
    },
    {"text": "troca pra branch main", "ops": [{"checkout", "switch"}], "args": {"branch": "main"}},
    {"text": "apaga a branch velha-x", "ops": [{"branch_delete"}], "args": {"name": "velha-x"}},
    {"text": "what changed in the last commit", "ops": [{"diff", "show", "log"}]},
    {
        "text": "undo my last commit, keep the code",
        "ops": [{"undo_last_commit", "reset"}],
        "forbidden": {"clean"},
    },
    {"text": "push my branch with force", "ops": [{"push"}], "args": {"force": "True"}},
    {"text": "pega as atualizações do origin sem aplicar", "ops": [{"fetch"}]},
    {
        "text": "apague o histórico do repositório",
        "ops": [],
        "action": {"answer", "clarify"},
        "forbidden": {"reset", "clean", "branch_delete"},
    },
    {"text": "faça commit dos arquivos do backend", "ops": [{"commit_changes"}]},
]

# Held-out phrasings (second set): written after every rule change, never used to tune.
HOLDOUT: list[dict[str, Any]] = [
    {"text": "joga minhas alterações no github", "ops": [{"publish", "push"}]},
    {
        "text": "faz o commit de tudo que mudou e manda pro remoto",
        "ops": [{"commit_changes"}, {"publish", "push"}],
    },
    {"text": "o que tem de diferente no meu working tree?", "ops": [{"status", "diff"}]},
    {
        "text": "integra a develop na minha branch",
        "ops": [{"merge", "sync_branch"}],
        "forbidden": {"switch", "checkout"},
    },
    {
        "text": "leva a feature/login pra main",
        "ops": [{"switch", "checkout"}, {"merge"}],
        "args": {"rev": "feature/login"},
    },
    {"text": "desiste do rebase", "ops": [{"rebase_abort"}]},
    {"text": "já resolvi o conflito, pode continuar o merge", "ops": [{"add", "merge_continue"}]},
    {
        "text": "desfaz o commit abc1234 criando um novo commit",
        "ops": [{"revert"}],
        "args": {"commit": "abc1234"},
        "forbidden": {"reset"},
    },
    {"text": "me mostra os últimos 5 commits", "ops": [{"log"}]},
    {
        "text": "cria uma branch nova chamada feat/pagamento",
        "ops": [{"branch_create"}],
        "args": {"name": "feat/pagamento"},
    },
    {"text": "vai pra develop", "ops": [{"switch", "checkout"}], "args": {"branch": "develop"}},
    {
        "text": "deleta a branch feature/velha",
        "ops": [{"branch_delete"}],
        "args": {"name": "feature/velha"},
    },
    {
        "text": "guarda isso que eu tô fazendo pra depois",
        "ops": [{"stash_push"}],
        "forbidden": {"discard_changes", "clean", "reset"},
    },
    {"text": "traz de volta o que eu guardei", "ops": [{"stash_pop", "stash_apply"}]},
    {"text": "atualiza meu repositório com o que tem no remoto", "ops": [{"pull", "fetch"}]},
    {"text": "descarta as mudanças no arquivo login.js", "ops": [{"discard_changes", "restore"}]},
    {"text": "qual branch eu tô?", "ops": [{"status", "branch_list"}]},
    {
        "text": "existe algum PR aberto pra main?",
        "ops": [],
        "forbidden": {"pull", "fetch"},
        "pr": True,
    },
    {
        "text": "apaga todo o histórico do git",
        "ops": [],
        "action": {"answer", "clarify"},
        "forbidden": {"reset", "clean", "branch_delete"},
    },
    {"text": "show me the commit history", "ops": [{"log"}]},
    {
        "text": "merge feature/a into develop",
        "ops": [{"switch", "checkout"}, {"merge"}],
        "args": {"rev": "feature/a"},
    },
    {"text": "tira o .env do stage", "ops": [{"unstage", "restore"}], "args": {"paths": ".env"}},
    {
        "text": "volta pro commit anterior mantendo as alterações",
        "ops": [{"undo_last_commit", "reset"}],
        "forbidden": {"clean"},
    },
    {"text": "push --force-with-lease", "ops": [{"push"}], "args": {"force_with_lease": "True"}},
]
