"""Prompts and JSON schemas for the local model (kept short: 3–4B models, 4k context)."""

from __future__ import annotations

from typing import Any

from grippo.engine.operations import CATALOG, catalog_for_prompt

# High-level intents the planner expands using repository state.
VIRTUAL_OPS: dict[str, str] = {
    "commit_changes": "commit_changes(request?:text, message?:message, paths?:paths, branch?:refname): preparar "
    "commit (GRIPPO escolhe arquivos relacionados e mensagem)",
    "publish": "publish(remote?:remote, branch?:refname): enviar commits locais ao remoto",
    "sync_branch": "sync_branch(base:rev, strategy?:[merge|rebase]): atualizar a branch atual "
    "com outra",
    "undo_last_commit": "undo_last_commit(keep_changes:bool): desfazer o último commit",
    "discard_changes": "discard_changes(paths?:paths): descartar alterações locais",
}

ALL_OPS = sorted(set(CATALOG) | set(VIRTUAL_OPS))

INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["execute", "answer", "clarify"]},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ALL_OPS},
                    "args": {"type": "object"},
                },
                "required": ["op", "args"],
            },
        },
        "reply": {"type": "string"},
        "question": {"type": "string"},
    },
    "required": ["action", "steps", "reply"],
}

RELEVANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"related": {"type": "boolean"}},
    "required": ["related"],
}

MESSAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
}

_EXAMPLES = """\
Pedido: quero fazer um commit das alterações do formulário
{"action":"execute","steps":[{"op":"commit_changes","args":{"request":"formulário"}}],"reply":"Vou preparar um commit com as alterações do formulário."}
Pedido: mostre o que foi alterado
{"action":"execute","steps":[{"op":"status","args":{}},{"op":"diff","args":{"stat":true}}],"reply":"Estas são as alterações atuais."}
Pedido: crie uma branch fix/login para essa correção
{"action":"execute","steps":[{"op":"branch_create","args":{"name":"fix/login","switch":true}}],"reply":"Vou criar a branch fix/login."}
Pedido: desfaça o último commit mas mantenha minhas alterações
{"action":"execute","steps":[{"op":"undo_last_commit","args":{"keep_changes":true}}],"reply":"Vou desfazer o último commit mantendo as alterações (reset --soft)."}
Pedido: quero desfazer as alterações do commit a1b2c3d sem apagar o histórico
{"action":"execute","steps":[{"op":"revert","args":{"commit":"a1b2c3d"}}],"reply":"Vou criar um commit que reverte a1b2c3d."}
Pedido: atualize minha branch com develop
{"action":"execute","steps":[{"op":"sync_branch","args":{"base":"develop"}}],"reply":"Vou atualizar a branch atual com develop."}
Pedido: envie minhas alterações para o remoto
{"action":"execute","steps":[{"op":"publish","args":{}}],"reply":"Vou enviar seus commits para o remoto."}
Pedido: faça cherry-pick do commit 9f8e7d6
{"action":"execute","steps":[{"op":"cherry_pick","args":{"commits":["9f8e7d6"]}}],"reply":"Vou aplicar o commit 9f8e7d6 na branch atual."}
Pedido: compare minha branch com main
{"action":"execute","steps":[{"op":"compare","args":{"base":"main"}},{"op":"diff","args":{"base":"main","stat":true}}],"reply":"Comparação com main."}
Pedido: mude para a branch
{"action":"clarify","steps":[],"reply":"","question":"Para qual branch você quer mudar?"}
Pedido: implemente a tela de login
{"action":"answer","steps":[],"reply":"O GRIPPO só executa operações Git; ele não implementa código. Depois de implementar, posso criar a branch e o commit."}"""


def system_prompt() -> str:
    catalog = catalog_for_prompt() + "\n" + "\n".join(f"- {v}" for v in VIRTUAL_OPS.values())
    return f"""Você é o GRIPPO: traduz pedidos em linguagem natural (pt-BR ou inglês) para \
operações Git do catálogo abaixo. Você NÃO escreve nem altera código e NÃO executa comandos: \
apenas escolhe operações; o GRIPPO valida, pede confirmação e executa.
Responda SOMENTE com JSON: {{"action":"execute|answer|clarify","steps":[{{"op":"nome","args":{{}}}}],"reply":"frase curta","question":"pergunta se clarify"}}
Regras:
1. Use somente operações e parâmetros do catálogo. "?" = opcional.
2. Commit → commit_changes. Push/enviar → publish. "atualizar com X" → sync_branch (strategy só se o usuário disser merge ou rebase).
3. Desfazer último commit mantendo alterações → undo_last_commit keep_changes=true.
4. Desfazer um commit sem reescrever histórico → revert.
5. Nunca escolha reset hard, clean, force push ou exclusão forçada se o usuário não pediu explicitamente.
6. Se faltar informação essencial (qual branch, qual commit), use action=clarify.
7. Pedidos que não são de Git (implementar, corrigir bug, revisar código): action=answer.
8. Use os nomes de branch/commit exatamente como aparecem no pedido ou no estado.
Catálogo:
{catalog}
Exemplos:
{_EXAMPLES}"""


def relevance_prompt(request: str, path: str, preview: str) -> list[dict[str, str]]:
    """One small yes/no decision per file: far more reliable on 3–4B models than asking
    for a set selection in one shot (measured: see docs/LLM.md)."""
    system = (
        "Você decide se UM arquivo alterado pertence ao commit que o usuário pediu. "
        "Se o pedido for genérico (ex.: 'faça commit', 'commita tudo'), responda related=true. "
        "Caso contrário, related=true só se o arquivo for claramente sobre o assunto do pedido. "
        'Responda JSON {"related": true|false}.'
    )
    user = f"Pedido: {request}\nArquivo: {path}\nConteúdo: {preview[:300]}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def message_prompt(request: str, files: str, diff: str) -> list[dict[str, str]]:
    system = (
        "Escreva UMA mensagem de commit Conventional Commit em inglês para as alterações "
        "abaixo: tipo(escopo): resumo no imperativo, até 72 caracteres, uma linha. "
        "Tipos: feat, fix, docs, style, refactor, perf, test, build, ci, chore. "
        'Responda só com JSON {"message":"..."}.'
    )
    user = (
        f"Pedido do usuário: {request or '(commit das alterações)'}\nArquivos:\n{files}\n"
        f"Diff (parcial):\n{diff}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
