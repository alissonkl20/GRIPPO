"""Catalog of Git operations GRIPPO knows how to run.

The LLM never produces argv. It proposes ``(operation name, args)``; this module
validates and normalizes the args, builds argv deterministically, assesses risk from
the args *and* the repository state, describes the impact for the confirmation
screen, and verifies the real outcome afterwards.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

from grippo.errors import ValidationError
from grippo.git import validate as v
from grippo.git.reader import GitReader
from grippo.git.repo import Repository
from grippo.git.runner import GitResult
from grippo.git.state import RepoState


class Risk(IntEnum):
    LOW = 0
    MODERATE = 1
    HIGH = 2

    @property
    def label(self) -> str:
        return ("baixo", "moderado", "alto")[self.value]


@dataclass(frozen=True)
class Param:
    kind: str  # rev range revs refname remote url paths path message text int bool choice
    required: bool = False
    default: Any = None
    choices: tuple[str, ...] = ()
    low: int = 0
    high: int = 1000


@dataclass
class Impact:
    consequences: list[str] = field(default_factory=list)
    recovery: str = ""
    files: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Verification:
    ok: bool
    message: str


@dataclass(frozen=True)
class OpContext:
    repo: Repository
    state: RepoState
    reader: GitReader
    # Operations planned before this one in the same plan (preview only).
    prior_ops: tuple[str, ...] = ()


Args = dict[str, Any]
Pre = dict[str, Any]


@dataclass(frozen=True)
class Operation:
    name: str
    summary: str
    params: Mapping[str, Param]
    build: Callable[[Args, OpContext], list[str]]
    risk: Risk = Risk.LOW
    mutating: bool = False
    network: bool = False
    risk_fn: Callable[[Args, OpContext], Risk] | None = None
    check_fn: Callable[[Args, OpContext], list[str]] | None = None
    impact_fn: Callable[[Args, OpContext], Impact] | None = None
    pre_fn: Callable[[Args, OpContext], Pre] | None = None
    verify_fn: Callable[[Args, Pre, OpContext, RepoState], Verification] | None = None
    # Operations that may legitimately stop half-way with conflicts.
    may_conflict: bool = False

    def assess_risk(self, args: Args, ctx: OpContext) -> Risk:
        if self.risk_fn is None:
            return self.risk
        return max(self.risk, self.risk_fn(args, ctx))

    def check(self, args: Args, ctx: OpContext) -> list[str]:
        return self.check_fn(args, ctx) if self.check_fn else []

    def impact(self, args: Args, ctx: OpContext) -> Impact:
        return self.impact_fn(args, ctx) if self.impact_fn else Impact()

    def pre(self, args: Args, ctx: OpContext) -> Pre:
        return self.pre_fn(args, ctx) if self.pre_fn else {}

    def verify(
        self, args: Args, pre: Pre, ctx: OpContext, after: RepoState, result: GitResult
    ) -> Verification:
        if not result.ok:
            return Verification(False, result.error_text() or f"git saiu com {result.returncode}")
        if self.verify_fn is None:
            return Verification(True, "concluído")
        return self.verify_fn(args, pre, ctx, after)


# --------------------------------------------------------------------------- params


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "1", "sim", "yes"}:
        return True
    if isinstance(value, str) and value.lower() in {"false", "0", "nao", "não", "no", ""}:
        return False
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ValidationError(f"valor booleano inválido: {value!r}")


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def normalize_param(name: str, param: Param, value: Any, repo_root: Path) -> Any:
    kind = param.kind
    if kind == "rev":
        return v.validate_revision(value)
    if kind == "range":
        return v.validate_revision(value, allow_range=True)
    if kind == "revs":
        items = [v.validate_revision(item) for item in _as_list(value)]
        if not 1 <= len(items) <= 50:
            raise ValidationError(f"{name}: informe de 1 a 50 commits")
        return items
    if kind == "refname":
        return v.validate_ref_name(value, name)
    if kind == "remote":
        return v.validate_remote_name(value)
    if kind == "url":
        return v.validate_url(value)
    if kind == "paths":
        items = [v.validate_path(repo_root, item) for item in _as_list(value)]
        if not 1 <= len(items) <= 1000:
            raise ValidationError(f"{name}: informe de 1 a 1000 caminhos")
        return list(dict.fromkeys(items))
    if kind == "path":
        return v.validate_path(repo_root, value)
    if kind == "message":
        return v.validate_message(value)
    if kind == "text":
        return v.validate_message(value, max_len=200)
    if kind == "int":
        return v.validate_int(value, low=param.low, high=param.high, what=name)
    if kind == "bool":
        return _as_bool(value)
    if kind == "choice":
        if value not in param.choices:
            raise ValidationError(f"{name} deve ser um de: {', '.join(param.choices)}")
        return value
    raise ValidationError(f"tipo de parâmetro desconhecido: {kind}")


def normalize_args(op: Operation, raw: Mapping[str, Any], repo_root: Path) -> Args:
    unknown = set(raw) - set(op.params)
    if unknown:
        raise ValidationError(f"{op.name}: parâmetros desconhecidos: {', '.join(sorted(unknown))}")
    out: Args = {}
    for name, param in op.params.items():
        value = raw.get(name)
        if value is None or value == [] or value == "":
            if param.required:
                raise ValidationError(f"{op.name}: parâmetro obrigatório ausente: {name}")
            value = param.default
            if value is None:
                continue
            out[name] = value
            continue
        out[name] = normalize_param(name, param, value, repo_root)
    return out


# --------------------------------------------------------------------------- helpers


def _flag(condition: Any, *flags: str) -> list[str]:
    return list(flags) if condition else []


def _opt(value: Any) -> list[str]:
    return [str(value)] if value is not None else []


def _covers(spec: str, path: str) -> bool:
    if spec == ".":
        return True
    spec = spec.rstrip("/")
    bare = path.rstrip("/")
    return (
        bare == spec
        or bare.startswith(spec + "/")
        or (path.endswith("/") and spec.startswith(path))
    )


def _matching(paths: list[str], candidates: list[str]) -> list[str]:
    return [c for c in candidates if any(_covers(p, c) for p in paths)]


def _no_in_progress(_: Args, ctx: OpContext) -> list[str]:
    if ctx.state.in_progress:
        return [f"há um {ctx.state.in_progress} em andamento; conclua ou aborte antes"]
    return []


def _needs_head(ctx: OpContext) -> list[str]:
    return [] if ctx.state.head else ["o repositório ainda não tem commits"]


def _rev_errors(ctx: OpContext, *revs: str | None) -> list[str]:
    return [f"revisão não encontrada: {r}" for r in revs if r and not ctx.reader.resolve(r)]


def _commit_list(commits: list[Any], limit: int = 15) -> list[str]:
    shown = [str(c) for c in commits[:limit]]
    if len(commits) > limit:
        shown.append(f"... e mais {len(commits) - limit}")
    return shown


def _ok(message: str) -> Verification:
    return Verification(True, message)


def _fail(message: str) -> Verification:
    return Verification(False, message)


def _default_remote(args: Args, ctx: OpContext) -> str:
    if args.get("remote"):
        return str(args["remote"])
    if ctx.state.upstream and "/" in ctx.state.upstream:
        return ctx.state.upstream.split("/", 1)[0]
    return "origin"


CATALOG: dict[str, Operation] = {}


def _register(op: Operation) -> Operation:
    if op.name in CATALOG:
        raise RuntimeError(f"operação duplicada: {op.name}")
    CATALOG[op.name] = op
    return op


def get_operation(name: str) -> Operation:
    try:
        return CATALOG[name]
    except KeyError:
        raise ValidationError(f"operação desconhecida: {name!r}") from None


# --------------------------------------------------------------------------- read-only

P = Param

_register(
    Operation(
        "status",
        "Estado do working tree e da branch",
        {},
        lambda a, c: ["status", "--short", "--branch"],
    )
)
_register(
    Operation(
        "diff",
        "Mostrar alterações (working tree, staged ou entre revisões)",
        {
            "staged": P("bool", default=False),
            "base": P("rev"),
            "target": P("rev"),
            "paths": P("paths"),
            "stat": P("bool", default=False),
        },
        lambda a, c: [
            "diff",
            *_flag(a.get("staged"), "--cached"),
            *_flag(a.get("stat"), "--stat"),
            *_opt(a.get("base")),
            *_opt(a.get("target")),
            "--",
            *a.get("paths", []),
        ],
    )
)
_register(
    Operation(
        "log",
        "Histórico de commits",
        {
            "rev": P("range"),
            "n": P("int", default=20, low=1, high=500),
            "paths": P("paths"),
            "graph": P("bool", default=False),
            "all": P("bool", default=False),
        },
        lambda a, c: [
            "log",
            f"-n{a['n']}",
            "--date=short",
            "--format=%h %ad %an%d %s",
            *_flag(a.get("graph"), "--graph"),
            *_flag(a.get("all"), "--all"),
            *_opt(a.get("rev")),
            "--",
            *a.get("paths", []),
        ],
    )
)
_register(
    Operation(
        "show",
        "Detalhes de um commit",
        {"rev": P("rev", default="HEAD"), "patch": P("bool", default=False)},
        lambda a, c: [
            "show",
            "--patch" if a.get("patch") else "--stat",
            "--format=fuller",
            a["rev"],
            "--",
        ],
    )
)
_register(
    Operation(
        "branch_list",
        "Listar branches (locais, remotas, mescladas)",
        {"all": P("bool", default=False), "merged": P("rev"), "no_merged": P("rev")},
        lambda a, c: [
            "branch",
            "--list",
            "-vv",
            *_flag(a.get("all"), "-a"),
            *(["--merged", a["merged"]] if a.get("merged") else []),
            *(["--no-merged", a["no_merged"]] if a.get("no_merged") else []),
        ],
    )
)
_register(Operation("remote_list", "Listar remotos", {}, lambda a, c: ["remote", "-v"]))
_register(
    Operation(
        "rev_parse",
        "Resolver uma revisão para SHA",
        {"rev": P("rev", True)},
        lambda a, c: ["rev-parse", "--verify", a["rev"]],
    )
)
_register(
    Operation(
        "ls_files",
        "Listar arquivos rastreados (ou não rastreados)",
        {"paths": P("paths"), "untracked": P("bool", default=False)},
        lambda a, c: [
            "ls-files",
            *_flag(a.get("untracked"), "--others", "--exclude-standard"),
            "--",
            *a.get("paths", []),
        ],
    )
)
_register(
    Operation(
        "reflog",
        "Histórico de movimentos do HEAD (recuperação)",
        {"n": P("int", default=20, low=1, high=500), "ref": P("rev")},
        lambda a, c: ["reflog", "show", f"-n{a['n']}", *_opt(a.get("ref"))],
    )
)
_register(
    Operation(
        "tag_list", "Listar tags", {}, lambda a, c: ["tag", "--list", "-n1", "--sort=-creatordate"]
    )
)
_register(Operation("stash_list", "Listar stashes", {}, lambda a, c: ["stash", "list"]))
_register(
    Operation(
        "stash_show",
        "Mostrar conteúdo de um stash",
        {"index": P("int", default=0, high=999)},
        lambda a, c: ["stash", "show", "--stat", f"stash@{{{a['index']}}}"],
    )
)
_register(
    Operation(
        "compare",
        "Comparar commits entre duas revisões (A...B)",
        {"base": P("rev", True), "target": P("rev", default="HEAD")},
        lambda a, c: [
            "log",
            "--left-right",
            "--format=%m %h %s",
            f"{a['base']}...{a['target']}",
            "--",
        ],
        check_fn=lambda a, c: _rev_errors(c, a["base"], a["target"]),
    )
)


# --------------------------------------------------------------------------- staging & commit


def _add_check(a: Args, c: OpContext) -> list[str]:
    errors = []
    changed = c.state.changed_paths
    for spec in a["paths"]:
        if not any(_covers(spec, path) or _covers(path, spec) for path in changed):
            errors.append(f"sem alterações em {spec}")
    if not a.get("allow_sensitive"):
        sensitive = [p for p in _matching(a["paths"], changed) if v.is_sensitive_path(p)]
        sensitive += [p for p in a["paths"] if v.is_sensitive_path(p)]
        if sensitive:
            errors.append("arquivo possivelmente sensível: " + ", ".join(sorted(set(sensitive))))
    return errors


def _add_impact(a: Args, c: OpContext) -> Impact:
    files = _matching(a["paths"], c.state.changed_paths)
    warnings = ["'.' adiciona TODAS as alterações do repositório"] if "." in a["paths"] else []
    return Impact(
        ["os arquivos passam para a staging area (index)"],
        "git restore --staged <arquivo> remove da staging area",
        files,
        [],
        warnings,
    )


def _add_verify(a: Args, p: Pre, c: OpContext, after: RepoState) -> Verification:
    pending = _matching(a["paths"], after.unstaged + list(after.untracked))
    if pending:
        return _fail("ainda não staged: " + ", ".join(pending))
    return _ok(f"{len(after.staged)} arquivo(s) na staging area")


_register(
    Operation(
        "add",
        "Adicionar arquivos específicos à staging area",
        {"paths": P("paths", True), "allow_sensitive": P("bool", default=False)},
        lambda a, c: ["add", "--", *a["paths"]],
        risk=Risk.MODERATE,
        mutating=True,
        risk_fn=lambda a, c: Risk.HIGH if a.get("allow_sensitive") else Risk.MODERATE,
        check_fn=_add_check,
        impact_fn=_add_impact,
        verify_fn=_add_verify,
    )
)


def _unstage_build(a: Args, c: OpContext) -> list[str]:
    if c.state.head is None:
        return ["rm", "--cached", "-r", "--quiet", "--", *a["paths"]]
    return ["restore", "--staged", "--", *a["paths"]]


_register(
    Operation(
        "unstage",
        "Remover arquivos da staging area (mantém as alterações)",
        {"paths": P("paths", True)},
        _unstage_build,
        risk=Risk.MODERATE,
        mutating=True,
        check_fn=lambda a, c: (
            [] if _matching(a["paths"], c.state.staged) else ["nenhum desses arquivos está staged"]
        ),
        impact_fn=lambda a, c: Impact(
            ["as alterações continuam no working tree"],
            "git add <arquivo>",
            _matching(a["paths"], c.state.staged),
        ),
        verify_fn=lambda a, p, c, after: (
            _fail("ainda staged")
            if _matching(a["paths"], after.staged)
            else _ok("removido da staging area")
        ),
    )
)


def _commit_check(a: Args, c: OpContext) -> list[str]:
    errors = []
    if c.state.conflicted:
        errors.append("há conflitos não resolvidos")
    if a.get("amend") and not c.state.head:
        errors.append("não há commit para emendar")
    if not a.get("amend") and not a.get("allow_empty") and not c.state.staged:
        errors.append("nada staged para commit")
    return errors


def _commit_risk(a: Args, c: OpContext) -> Risk:
    if a.get("amend") and c.state.head:
        head = c.reader.commits("HEAD", limit=1)
        if c.reader.published_commits(head):
            return Risk.HIGH
    return Risk.MODERATE


def _commit_impact(a: Args, c: OpContext) -> Impact:
    branch = c.state.branch or "HEAD destacado"
    if a.get("amend"):
        return Impact(
            [
                f"substitui o último commit ({c.state.short_head}) em {branch}",
                "o hash do commit muda; se já foi enviado, exigirá force push",
            ],
            f"git reset --soft {c.state.head} restaura o commit original" if c.state.head else "",
            c.state.staged,
        )
    recovery = "git reset --soft HEAD~1 desfaz o commit mantendo as alterações"
    if not c.state.staged:
        # Previewed before an `add` step of the same plan: the files are listed there.
        return Impact(
            [f"cria um novo commit em {branch} com os arquivos do passo anterior"], recovery
        )
    return Impact(
        [f"cria um novo commit em {branch} com {len(c.state.staged)} arquivo(s)"],
        recovery,
        c.state.staged,
        warnings=[f"{len(c.state.unstaged)} arquivo(s) não staged ficam de fora"]
        if c.state.unstaged
        else [],
    )


def _commit_verify(a: Args, p: Pre, c: OpContext, after: RepoState) -> Verification:
    if after.head == c.state.head:
        return _fail("HEAD não mudou")
    subject = a["message"].splitlines()[0].strip()
    if (after.head_subject or "").strip() != subject:
        return _fail(f"commit criado mas assunto difere: {after.head_subject!r}")
    return _ok(f"commit {after.short_head} criado")


_register(
    Operation(
        "commit",
        "Criar commit com o que está staged",
        {
            "message": P("message", True),
            "amend": P("bool", default=False),
            "allow_empty": P("bool", default=False),
        },
        lambda a, c: [
            "commit",
            f"--message={a['message']}",
            *_flag(a.get("amend"), "--amend"),
            *_flag(a.get("allow_empty"), "--allow-empty"),
        ],
        risk=Risk.MODERATE,
        mutating=True,
        risk_fn=_commit_risk,
        check_fn=_commit_check,
        impact_fn=_commit_impact,
        verify_fn=_commit_verify,
    )
)


# --------------------------------------------------------------------------- branches


def _branch_create_check(a: Args, c: OpContext) -> list[str]:
    errors = []
    if c.reader.branch_exists(a["name"]):
        errors.append(f"a branch {a['name']} já existe")
    errors += _rev_errors(c, a.get("start"))
    if not a.get("switch") and not c.state.head and not a.get("start"):
        errors += _needs_head(c)
    return errors


def _branch_create_verify(a: Args, p: Pre, c: OpContext, after: RepoState) -> Verification:
    if not c.reader.branch_exists(a["name"]):
        return _fail("branch não encontrada após criação")
    if a.get("switch") and after.branch != a["name"]:
        return _fail(f"branch criada, mas HEAD está em {after.branch}")
    return _ok(f"branch {a['name']} criada" + (" e ativa" if a.get("switch") else ""))


_register(
    Operation(
        "branch_create",
        "Criar branch (opcionalmente já alternando para ela)",
        {"name": P("refname", True), "start": P("rev"), "switch": P("bool", default=True)},
        lambda a, c: (
            ["switch", "-c", a["name"], *_opt(a.get("start"))]
            if a.get("switch")
            else ["branch", a["name"], *_opt(a.get("start"))]
        ),
        risk=Risk.MODERATE,
        mutating=True,
        check_fn=_branch_create_check,
        impact_fn=lambda a, c: Impact(
            [f"cria a branch {a['name']} a partir de {a.get('start') or 'HEAD'}"]
            + ([f"HEAD passa para {a['name']}"] if a.get("switch") else []),
            f"git branch -d {a['name']}",
        ),
        verify_fn=_branch_create_verify,
    )
)


def _rename_check(a: Args, c: OpContext) -> list[str]:
    errors = []
    old = a.get("old") or c.state.branch
    if not old:
        errors.append("HEAD destacado: informe a branch a renomear")
    elif not c.reader.branch_exists(old):
        errors.append(f"branch inexistente: {old}")
    if c.reader.branch_exists(a["new"]):
        errors.append(f"a branch {a['new']} já existe")
    return errors


_register(
    Operation(
        "branch_rename",
        "Renomear branch",
        {"new": P("refname", True), "old": P("refname")},
        lambda a, c: ["branch", "-m", *_opt(a.get("old")), a["new"]],
        risk=Risk.MODERATE,
        mutating=True,
        check_fn=_rename_check,
        impact_fn=lambda a, c: Impact(
            [
                f"renomeia {a.get('old') or c.state.branch} para {a['new']}",
                "o upstream remoto (se houver) não é renomeado",
            ],
            f"git branch -m {a['new']} {a.get('old') or c.state.branch}",
        ),
        verify_fn=lambda a, p, c, after: (
            _ok("branch renomeada")
            if c.reader.branch_exists(a["new"])
            else _fail("branch nova não encontrada")
        ),
    )
)


def _switch_check(a: Args, c: OpContext) -> list[str]:
    errors = _no_in_progress(a, c)
    if a["branch"] == c.state.branch:
        errors.append(f"já está na branch {a['branch']}")
    elif not c.reader.branch_exists(a["branch"]) and not any(
        r.split("/", 1)[1] == a["branch"] for r in c.reader.remote_branches()
    ):
        errors.append(f"branch inexistente: {a['branch']}")
    return errors


def _switch_impact(a: Args, c: OpContext) -> Impact:
    warnings = []
    if c.state.staged or c.state.unstaged:
        warnings.append(
            "há alterações locais: elas acompanham a troca; o Git recusa se "
            "houver conflito com a branch de destino"
        )
    return Impact(
        [f"HEAD passa de {c.state.branch or c.state.short_head} para {a['branch']}"],
        f"git switch {c.state.branch}" if c.state.branch else "",
        warnings=warnings,
        files=c.state.staged + c.state.unstaged,
    )


_register(
    Operation(
        "switch",
        "Alternar para outra branch",
        {"branch": P("refname", True)},
        lambda a, c: ["switch", a["branch"]],
        risk=Risk.MODERATE,
        mutating=True,
        check_fn=_switch_check,
        impact_fn=_switch_impact,
        verify_fn=lambda a, p, c, after: (
            _ok(f"agora em {after.branch}")
            if after.branch == a["branch"]
            else _fail(f"HEAD está em {after.branch}")
        ),
    )
)


def _checkout_pre(a: Args, c: OpContext) -> Pre:
    return {"sha": c.reader.resolve(a["rev"])}


_register(
    Operation(
        "checkout",
        "Fazer checkout de uma revisão (pode destacar o HEAD)",
        {"rev": P("rev", True)},
        lambda a, c: ["checkout", a["rev"], "--"],
        risk=Risk.MODERATE,
        mutating=True,
        check_fn=lambda a, c: _no_in_progress(a, c) + _rev_errors(c, a["rev"]),
        impact_fn=lambda a, c: Impact(
            [f"HEAD passa para {a['rev']}"],
            f"git switch {c.state.branch}" if c.state.branch else "",
            files=c.state.staged + c.state.unstaged,
            warnings=(
                []
                if c.reader.branch_exists(a["rev"])
                else ["HEAD ficará destacado (detached): novos commits não pertencem a branch"]
            )
            + (
                ["há alterações locais; o Git recusa se houver conflito"]
                if c.state.staged or c.state.unstaged
                else []
            ),
        ),
        pre_fn=_checkout_pre,
        verify_fn=lambda a, p, c, after: (
            _ok(f"HEAD em {after.short_head}")
            if after.head == p.get("sha")
            else _fail("HEAD não corresponde à revisão")
        ),
    )
)


def _delete_check(a: Args, c: OpContext) -> list[str]:
    if a["name"] == c.state.branch:
        return ["não é possível excluir a branch atual"]
    if not c.reader.branch_exists(a["name"]):
        return [f"branch inexistente: {a['name']}"]
    return []


def _delete_impact(a: Args, c: OpContext) -> Impact:
    unique = c.reader.unmerged_commits(a["name"])
    sha = c.reader.resolve(a["name"]) or "?"
    warnings = []
    if unique and not a.get("force"):
        warnings.append("a branch tem commits não mesclados: o Git vai recusar sem força (-D)")
    if unique and a.get("force"):
        warnings.append(f"{len(unique)} commit(s) só existem nesta branch")
    return Impact(
        [f"exclui a branch local {a['name']} (aponta para {sha[:8]})"],
        f"git branch {a['name']} {sha}",
        commits=_commit_list(unique),
        warnings=warnings,
    )


_register(
    Operation(
        "branch_delete",
        "Excluir branch local",
        {"name": P("refname", True), "force": P("bool", default=False)},
        lambda a, c: ["branch", "-D" if a.get("force") else "-d", a["name"]],
        risk=Risk.MODERATE,
        mutating=True,
        risk_fn=lambda a, c: Risk.HIGH if a.get("force") else Risk.MODERATE,
        check_fn=_delete_check,
        impact_fn=_delete_impact,
        verify_fn=lambda a, p, c, after: (
            _fail("branch ainda existe")
            if c.reader.branch_exists(a["name"])
            else _ok(f"branch {a['name']} excluída")
        ),
    )
)
_register(
    Operation(
        "branch_set_upstream",
        "Definir upstream da branch",
        {"upstream": P("rev", True), "branch": P("refname")},
        lambda a, c: ["branch", f"--set-upstream-to={a['upstream']}", *_opt(a.get("branch"))],
        risk=Risk.MODERATE,
        mutating=True,
        check_fn=lambda a, c: _rev_errors(c, a["upstream"]),
        impact_fn=lambda a, c: Impact(
            [f"{a.get('branch') or c.state.branch} passa a acompanhar {a['upstream']}"],
            "git branch --unset-upstream",
        ),
    )
)


# --------------------------------------------------------------------------- history


def _resolved(rev_key: str) -> Callable[[Args, OpContext], Pre]:
    def pre(a: Args, c: OpContext) -> Pre:
        return {"sha": c.reader.resolve(a[rev_key]), "head": c.state.head}

    return pre


def _merge_impact(a: Args, c: OpContext) -> Impact:
    incoming = c.reader.commits(f"HEAD..{a['rev']}")
    warnings = [] if incoming else ["nada a integrar: a branch já contém essa revisão"]
    if c.state.staged or c.state.unstaged:
        warnings.append("há alterações locais; o Git recusa se o merge as sobrescrever")
    return Impact(
        [
            f"integra {len(incoming)} commit(s) de {a['rev']} em {c.state.branch or 'HEAD'}",
            "pode gerar conflitos que precisarão ser resolvidos",
        ],
        f"durante conflito: merge_abort; depois: git reset --merge {c.state.head}",
        commits=_commit_list(incoming),
        warnings=warnings,
    )


_register(
    Operation(
        "merge",
        "Mesclar uma branch/revisão na branch atual",
        {
            "rev": P("rev", True),
            "no_ff": P("bool", default=False),
            "ff_only": P("bool", default=False),
        },
        lambda a, c: [
            "merge",
            "--no-edit",
            *_flag(a.get("no_ff"), "--no-ff"),
            *_flag(a.get("ff_only"), "--ff-only"),
            a["rev"],
        ],
        risk=Risk.MODERATE,
        mutating=True,
        may_conflict=True,
        check_fn=lambda a, c: (
            _no_in_progress(a, c)
            + _needs_head(c)
            + _rev_errors(c, a["rev"])
            + (["no_ff e ff_only são incompatíveis"] if a.get("no_ff") and a.get("ff_only") else [])
        ),
        impact_fn=_merge_impact,
        pre_fn=_resolved("rev"),
        verify_fn=lambda a, p, c, after: (
            _ok("merge concluído")
            if after.head and c.reader.is_ancestor(p["sha"], after.head)
            else _fail("merge incompleto")
        ),
    )
)


def _cherry_impact(a: Args, c: OpContext) -> Impact:
    commits = [c.reader.commits(r, limit=1)[0] for r in a["commits"] if c.reader.resolve(r)]
    return Impact(
        [
            f"aplica {len(commits)} commit(s) sobre {c.state.branch or 'HEAD'}",
            "cria novos commits (novos hashes); pode gerar conflitos",
        ],
        f"durante conflito: cherry_pick_abort; depois: git reset --hard {c.state.head}",
        commits=_commit_list(commits),
    )


_register(
    Operation(
        "cherry_pick",
        "Aplicar commits específicos na branch atual",
        {"commits": P("revs", True)},
        lambda a, c: ["cherry-pick", *a["commits"]],
        risk=Risk.MODERATE,
        mutating=True,
        may_conflict=True,
        check_fn=lambda a, c: (
            _no_in_progress(a, c) + _needs_head(c) + _rev_errors(c, *a["commits"])
        ),
        impact_fn=_cherry_impact,
        pre_fn=lambda a, c: {"head": c.state.head},
        verify_fn=lambda a, p, c, after: (
            _ok(f"HEAD agora em {after.short_head}")
            if after.head != p["head"]
            else _fail("nenhum commit aplicado")
        ),
    )
)


def _revert_check(a: Args, c: OpContext) -> list[str]:
    errors = _no_in_progress(a, c) + _needs_head(c) + _rev_errors(c, a["commit"])
    if not errors and c.reader.parent_count(a["commit"]) > 1 and not a.get("mainline"):
        errors.append("é um commit de merge: informe mainline (geralmente 1)")
    return errors


def _revert_impact(a: Args, c: OpContext) -> Impact:
    target = c.reader.commits(a["commit"], limit=1)
    return Impact(
        [
            "cria um NOVO commit que desfaz as alterações do commit indicado",
            "o histórico existente é preservado (seguro para branches publicadas)",
        ],
        "git reset --hard HEAD~1 (se ainda não enviado) ou reverter o revert",
        commits=_commit_list(target),
    )


_register(
    Operation(
        "revert",
        "Reverter um commit criando um commit inverso",
        {"commit": P("rev", True), "mainline": P("int", low=1, high=9)},
        lambda a, c: [
            "revert",
            "--no-edit",
            *(["-m", str(a["mainline"])] if a.get("mainline") else []),
            a["commit"],
        ],
        risk=Risk.MODERATE,
        mutating=True,
        may_conflict=True,
        check_fn=_revert_check,
        impact_fn=_revert_impact,
        pre_fn=lambda a, c: {"head": c.state.head},
        verify_fn=lambda a, p, c, after: (
            _ok(f"commit de revert {after.short_head} criado")
            if after.head != p["head"]
            else _fail("nenhum commit criado")
        ),
    )
)


def _reset_risk(a: Args, c: OpContext) -> Risk:
    if a["mode"] == "hard":
        return Risk.HIGH
    removed = c.reader.commits(f"{a['target']}..HEAD") if c.state.head else []
    return Risk.HIGH if c.reader.published_commits(removed) else Risk.MODERATE


def _reset_impact(a: Args, c: OpContext) -> Impact:
    removed = c.reader.commits(f"{a['target']}..HEAD") if c.state.head else []
    published = c.reader.published_commits(removed)
    mode = a["mode"]
    consequences = [f"move {c.state.branch or 'HEAD'} para {a['target']}"]
    if removed:
        consequences.append(f"{len(removed)} commit(s) deixam de fazer parte da branch")
    files: list[str] = []
    warnings: list[str] = []
    if mode == "soft":
        consequences.append("as alterações desses commits ficam staged")
    elif mode == "mixed":
        consequences.append("as alterações ficam no working tree, fora da staging area")
    else:
        files = sorted(set(c.state.staged + c.state.unstaged))
        consequences.append("DESCARTA todas as alterações locais de arquivos rastreados")
        if files:
            warnings.append(
                f"{len(files)} arquivo(s) com alterações não commitadas serão "
                "perdidos e NÃO podem ser recuperados pelo reflog"
            )
        if c.state.untracked:
            consequences.append("arquivos não rastreados não são removidos")
    if published:
        warnings.append(
            f"{len(published)} commit(s) já estão no remoto: reescrever exigirá "
            "force push e afeta colaboradores"
        )
    recovery = (
        f"git reset --hard {c.state.head} (commits continuam no reflog)" if c.state.head else ""
    )
    return Impact(consequences, recovery, files, _commit_list(removed), warnings)


_register(
    Operation(
        "reset",
        "Mover a branch para outra revisão (soft/mixed/hard)",
        {
            "mode": P("choice", True, choices=("soft", "mixed", "hard")),
            "target": P("rev", default="HEAD"),
        },
        lambda a, c: ["reset", f"--{a['mode']}", a["target"]],
        risk=Risk.MODERATE,
        mutating=True,
        risk_fn=_reset_risk,
        check_fn=lambda a, c: _needs_head(c) + _rev_errors(c, a["target"]),
        impact_fn=_reset_impact,
        pre_fn=_resolved("target"),
        verify_fn=lambda a, p, c, after: (
            _ok(f"HEAD em {after.short_head}")
            if after.head == p["sha"]
            else _fail("HEAD não corresponde ao alvo")
        ),
    )
)


def _rebase_check(a: Args, c: OpContext) -> list[str]:
    errors = _no_in_progress(a, c) + _needs_head(c) + _rev_errors(c, a["upstream"], a.get("onto"))
    if c.state.staged or c.state.unstaged:
        errors.append("rebase exige working tree limpo: faça commit ou stash antes")
    return errors


def _rebase_commits(a: Args, c: OpContext) -> list[Any]:
    return c.reader.commits(f"{a['upstream']}..HEAD")


def _rebase_impact(a: Args, c: OpContext) -> Impact:
    commits = _rebase_commits(a, c)
    published = c.reader.published_commits(commits)
    warnings = []
    if published:
        warnings.append(f"{len(published)} commit(s) já publicados: será preciso force push")
    if not commits:
        warnings.append("nenhum commit local para reaplicar")
    return Impact(
        [
            f"reaplica {len(commits)} commit(s) de {c.state.branch} sobre "
            f"{a.get('onto') or a['upstream']}",
            "os hashes dos commits mudam: o histórico é reescrito",
            "pode parar em conflitos (rebase_continue / rebase_abort)",
        ],
        f"durante: rebase_abort; depois: git reset --hard {c.state.head}",
        commits=_commit_list(commits),
        warnings=warnings,
    )


_register(
    Operation(
        "rebase",
        "Reaplicar commits da branch atual sobre outra base",
        {"upstream": P("rev", True), "onto": P("rev")},
        lambda a, c: ["rebase", *(["--onto", a["onto"]] if a.get("onto") else []), a["upstream"]],
        risk=Risk.MODERATE,
        mutating=True,
        may_conflict=True,
        risk_fn=lambda a, c: (
            Risk.HIGH if c.reader.published_commits(_rebase_commits(a, c)) else Risk.MODERATE
        ),
        check_fn=_rebase_check,
        impact_fn=_rebase_impact,
        pre_fn=_resolved("upstream"),
        verify_fn=lambda a, p, c, after: (
            _ok("rebase concluído")
            if after.head and c.reader.is_ancestor(p["sha"], after.head)
            else _fail("rebase incompleto")
        ),
    )
)


def _sequencer(kind: str, git_name: str, action: str, risk: Risk, summary: str) -> None:
    def check(a: Args, c: OpContext) -> list[str]:
        if c.state.in_progress != kind:
            return [f"não há {kind} em andamento"]
        if action == "continue" and c.state.conflicted:
            return ["ainda há conflitos: resolva e faça add dos arquivos"]
        return []

    consequences = {
        "abort": [
            f"cancela o {kind} e volta ao estado anterior",
            "resoluções de conflito feitas até agora são descartadas",
        ],
        "continue": [f"prossegue o {kind} com os arquivos resolvidos"],
        "skip": [f"PULA o commit atual do {kind}: suas alterações são descartadas"],
    }[action]

    _register(
        Operation(
            f"{kind.replace('-', '_')}_{action}",
            summary,
            {},
            lambda a, c: [git_name, f"--{action}"],
            risk=risk,
            mutating=True,
            may_conflict=action != "abort",
            check_fn=check,
            impact_fn=lambda a, c: Impact(
                consequences,
                "git reflog mostra os estados anteriores",
                files=c.state.conflicted + c.state.staged,
            ),
            verify_fn=lambda a, p, c, after: (
                _ok(f"{kind} {action}: ok")
                if after.in_progress != kind
                else _fail(f"{kind} ainda em andamento")
            ),
        )
    )


for _kind, _git in (
    ("merge", "merge"),
    ("cherry-pick", "cherry-pick"),
    ("revert", "revert"),
    ("rebase", "rebase"),
):
    _sequencer(_kind, _git, "abort", Risk.MODERATE, f"Abortar {_kind} em andamento")
    _sequencer(_kind, _git, "continue", Risk.MODERATE, f"Continuar {_kind} após resolver")
for _kind in ("cherry-pick", "rebase"):
    _sequencer(_kind, _kind, "skip", Risk.HIGH, f"Pular commit atual do {_kind}")


# --------------------------------------------------------------------------- stash


def _stash_ref(a: Args) -> str:
    return f"stash@{{{a['index']}}}"


def _stash_index_check(a: Args, c: OpContext) -> list[str]:
    return [] if a["index"] < c.state.stash_count else [f"stash inexistente: {_stash_ref(a)}"]


_register(
    Operation(
        "stash_push",
        "Guardar alterações locais no stash",
        {"message": P("text"), "include_untracked": P("bool", default=False), "paths": P("paths")},
        lambda a, c: [
            "stash",
            "push",
            *_flag(a.get("include_untracked"), "--include-untracked"),
            *([f"--message={a['message']}"] if a.get("message") else []),
            "--",
            *a.get("paths", []),
        ],
        risk=Risk.MODERATE,
        mutating=True,
        check_fn=lambda a, c: (
            []
            if (
                c.state.staged
                or c.state.unstaged
                or (a.get("include_untracked") and c.state.untracked)
            )
            else ["nada para guardar no stash"]
        ),
        impact_fn=lambda a, c: Impact(
            ["as alterações saem do working tree e ficam guardadas no stash"],
            "stash_pop restaura as alterações",
            files=c.state.staged
            + c.state.unstaged
            + (list(c.state.untracked) if a.get("include_untracked") else []),
            warnings=[]
            if a.get("include_untracked") or not c.state.untracked
            else ["arquivos não rastreados permanecem (use include_untracked)"],
        ),
        pre_fn=lambda a, c: {"count": c.state.stash_count},
        verify_fn=lambda a, p, c, after: (
            _ok("alterações guardadas")
            if after.stash_count == p["count"] + 1
            else _fail("stash não criado")
        ),
    )
)
for _name, _action, _risk in (
    ("stash_pop", "pop", Risk.MODERATE),
    ("stash_apply", "apply", Risk.MODERATE),
    ("stash_drop", "drop", Risk.HIGH),
):
    _register(
        Operation(
            _name,
            {
                "pop": "Restaurar e remover um stash",
                "apply": "Aplicar stash sem removê-lo",
                "drop": "Excluir um stash",
            }[_action],
            {"index": P("int", default=0, high=999)},
            (lambda act: lambda a, c: ["stash", act, _stash_ref(a)])(_action),
            risk=_risk,
            mutating=True,
            may_conflict=_action != "drop",
            check_fn=_stash_index_check,
            impact_fn=(
                lambda act: (
                    lambda a, c: Impact(
                        {
                            "pop": ["aplica o stash no working tree e o remove da lista"],
                            "apply": ["aplica o stash no working tree e o mantém na lista"],
                            "drop": ["remove o stash definitivamente da lista"],
                        }[act],
                        "recuperável apenas via git fsck --unreachable"
                        if act == "drop"
                        else "o Git mantém o stash se houver conflito",
                        warnings=["há alterações locais: pode haver conflito"]
                        if act != "drop" and (c.state.staged or c.state.unstaged)
                        else [],
                    )
                )
            )(_action),
            pre_fn=lambda a, c: {"count": c.state.stash_count},
            verify_fn=(
                lambda act: (
                    lambda a, p, c, after: (
                        _ok("stash aplicado")
                        if (after.stash_count == p["count"] - (0 if act == "apply" else 1))
                        else _fail("contagem de stash inesperada")
                    )
                )
            )(_action),
        )
    )


# --------------------------------------------------------------------------- sync


def _remote_check(a: Args, c: OpContext) -> list[str]:
    remote = _default_remote(a, c)
    return [] if remote in c.reader.remotes() else [f"remoto inexistente: {remote}"]


_register(
    Operation(
        "fetch",
        "Buscar atualizações do remoto (não altera suas branches)",
        {"remote": P("remote"), "all": P("bool", default=False), "prune": P("bool", default=False)},
        lambda a, c: [
            "fetch",
            *_flag(a.get("prune"), "--prune"),
            *(["--all"] if a.get("all") else [_default_remote(a, c)]),
        ],
        risk=Risk.LOW,
        mutating=True,
        network=True,
        check_fn=lambda a, c: [] if a.get("all") else _remote_check(a, c),
        impact_fn=lambda a, c: Impact(["atualiza apenas as referências remotas (origin/*)"]),
    )
)


def _pull_check(a: Args, c: OpContext) -> list[str]:
    errors = _no_in_progress(a, c) + (_needs_head(c) if a["mode"] == "rebase" else [])
    if not a.get("branch") and not c.state.upstream:
        errors.append("a branch não tem upstream: informe remote e branch")
    if a.get("remote") or not c.state.upstream:
        errors += _remote_check(a, c)
    if a["mode"] == "rebase" and (c.state.staged or c.state.unstaged):
        errors.append("pull com rebase exige working tree limpo")
    return errors


def _pull_build(a: Args, c: OpContext) -> list[str]:
    mode = {"ff-only": "--ff-only", "merge": "--no-rebase", "rebase": "--rebase"}[a["mode"]]
    target = (
        [_default_remote(a, c), a["branch"]]
        if a.get("branch")
        else ([a["remote"]] if a.get("remote") else [])
    )
    return ["pull", mode, *target]


_register(
    Operation(
        "pull",
        "Buscar e integrar alterações do remoto",
        {
            "remote": P("remote"),
            "branch": P("refname"),
            "mode": P("choice", default="ff-only", choices=("ff-only", "merge", "rebase")),
        },
        _pull_build,
        risk=Risk.MODERATE,
        mutating=True,
        network=True,
        may_conflict=True,
        check_fn=_pull_check,
        impact_fn=lambda a, c: Impact(
            [f"integra commits remotos em {c.state.branch} ({a['mode']})"]
            + (
                ["ff-only falha se as históricos divergirem (nada é alterado)"]
                if a["mode"] == "ff-only"
                else ["pode gerar conflitos"]
            ),
            f"git reset --hard {c.state.head} desfaz a integração" if c.state.head else "",
            warnings=[f"a branch está {c.state.behind} commit(s) atrás do upstream"]
            if c.state.behind
            else [],
        ),
    )
)


def _push_branch(a: Args, c: OpContext) -> str | None:
    return a.get("branch") or c.state.branch


def _push_check(a: Args, c: OpContext) -> list[str]:
    errors = _remote_check(a, c)
    if not _push_branch(a, c):
        errors.append("HEAD destacado: informe a branch")
    if a.get("force") and a.get("force_with_lease"):
        errors.append("escolha force ou force_with_lease, não ambos")
    if not c.state.head:
        errors += _needs_head(c)
    return errors


def _push_commits(a: Args, c: OpContext) -> list[Any]:
    remote, branch = _default_remote(a, c), _push_branch(a, c)
    tracking = f"{remote}/{branch}"
    if c.reader.resolve(tracking):
        return c.reader.commits(f"{tracking}..HEAD")
    return c.reader.commits("HEAD", "--not", "--remotes")


def _push_impact(a: Args, c: OpContext) -> Impact:
    remote, branch = _default_remote(a, c), _push_branch(a, c)
    commits = _push_commits(a, c)
    pending = "commit" in c.prior_ops
    consequences = [
        f"envia {len(commits)} commit(s) para {remote}/{branch}"
        + (" + o commit criado no passo anterior" if pending else "")
    ]
    warnings = []
    forced = a.get("force") or a.get("force_with_lease")
    if forced:
        consequences.append(
            "SOBRESCREVE o histórico remoto: commits remotos ausentes "
            "localmente deixam de existir na branch remota"
        )
        if a.get("force_with_lease"):
            consequences.append("--force-with-lease recusa se o remoto mudou desde o último fetch")
        if branch in _PROTECTED:
            warnings.append(f"{branch} é uma branch protegida/compartilhada")
    elif c.state.behind:
        warnings.append(
            f"a branch está {c.state.behind} commit(s) atrás: o push será "
            "rejeitado; faça pull antes"
        )
    if not commits and not forced and not pending:
        warnings.append("nada novo para enviar")
    if a.get("set_upstream"):
        consequences.append(f"define {remote}/{branch} como upstream")
    recovery = (
        "outros clones ou o reflog do servidor podem conter os commits sobrescritos"
        if forced
        else "git revert dos commits enviados (não reescreva histórico público)"
    )
    return Impact(consequences, recovery, commits=_commit_list(commits), warnings=warnings)


_PROTECTED: set[str] = {"main", "master", "develop"}


def set_protected_branches(names: list[str]) -> None:
    _PROTECTED.clear()
    _PROTECTED.update(names)


_register(
    Operation(
        "push",
        "Enviar commits para o remoto",
        {
            "remote": P("remote"),
            "branch": P("refname"),
            "set_upstream": P("bool", default=False),
            "force": P("bool", default=False),
            "force_with_lease": P("bool", default=False),
        },
        lambda a, c: [
            "push",
            *_flag(a.get("set_upstream"), "--set-upstream"),
            *_flag(a.get("force_with_lease"), "--force-with-lease"),
            *_flag(a.get("force"), "--force"),
            _default_remote(a, c),
            f"HEAD:refs/heads/{_push_branch(a, c)}",
        ],
        risk=Risk.MODERATE,
        mutating=True,
        network=True,
        risk_fn=lambda a, c: (
            Risk.HIGH if a.get("force") or a.get("force_with_lease") else Risk.MODERATE
        ),
        check_fn=_push_check,
        impact_fn=_push_impact,
        verify_fn=lambda a, p, c, after: (
            _ok("remoto atualizado")
            if c.reader.resolve(f"{_default_remote(a, c)}/{_push_branch(a, c)}") == after.head
            else _fail("a referência remota não corresponde ao HEAD local")
        ),
    )
)
_register(
    Operation(
        "push_delete",
        "Excluir branch remota",
        {"remote": P("remote", default="origin"), "branch": P("refname", True)},
        lambda a, c: ["push", a["remote"], "--delete", f"refs/heads/{a['branch']}"],
        risk=Risk.HIGH,
        mutating=True,
        network=True,
        check_fn=lambda a, c: _remote_check(a, c),
        impact_fn=lambda a, c: Impact(
            [f"exclui a branch {a['branch']} do remoto {a['remote']} para todos os colaboradores"],
            f"git push {a['remote']} {c.reader.resolve(a['remote'] + '/' + a['branch']) or '<sha>'}"
            f":refs/heads/{a['branch']}",
        ),
        verify_fn=lambda a, p, c, after: (
            _fail("branch remota ainda existe")
            if c.reader.resolve(f"{a['remote']}/{a['branch']}")
            else _ok("branch remota excluída")
        ),
    )
)
_register(
    Operation(
        "push_tag",
        "Enviar tag para o remoto",
        {"remote": P("remote", default="origin"), "tag": P("refname", True)},
        lambda a, c: ["push", a["remote"], f"refs/tags/{a['tag']}"],
        risk=Risk.MODERATE,
        mutating=True,
        network=True,
        check_fn=lambda a, c: (
            _remote_check(a, c)
            + ([] if c.reader.tag_exists(a["tag"]) else [f"tag inexistente: {a['tag']}"])
        ),
        impact_fn=lambda a, c: Impact([f"publica a tag {a['tag']} em {a['remote']}"]),
    )
)
_register(
    Operation(
        "tag_delete_remote",
        "Excluir tag do remoto",
        {"remote": P("remote", default="origin"), "tag": P("refname", True)},
        lambda a, c: ["push", a["remote"], "--delete", f"refs/tags/{a['tag']}"],
        risk=Risk.HIGH,
        mutating=True,
        network=True,
        check_fn=lambda a, c: _remote_check(a, c),
        impact_fn=lambda a, c: Impact(
            [
                f"exclui a tag {a['tag']} do remoto {a['remote']}",
                "quem já baixou a tag continua com ela",
            ],
            f"push_tag {a['tag']} se ela existir localmente",
        ),
    )
)


# --------------------------------------------------------------------------- files


def _restore_impact(a: Args, c: OpContext) -> Impact:
    files = _matching(a["paths"], c.state.unstaged) if not a.get("source") else a["paths"]
    stat = c.reader.diff_stat("--", *a["paths"])
    return Impact(
        ["DESCARTA as alterações não commitadas desses arquivos no working tree"]
        + ([stat] if stat else []),
        "não há recuperação: alterações não commitadas não ficam no reflog",
        files=files,
    )


_register(
    Operation(
        "restore",
        "Descartar alterações locais de arquivos",
        {"paths": P("paths", True), "source": P("rev")},
        lambda a, c: [
            "restore",
            "--worktree",
            *([f"--source={a['source']}"] if a.get("source") else []),
            "--",
            *a["paths"],
        ],
        risk=Risk.HIGH,
        mutating=True,
        check_fn=lambda a, c: (
            _rev_errors(c, a.get("source"))
            + (
                []
                if a.get("source") or _matching(a["paths"], c.state.unstaged)
                else ["nenhum desses arquivos tem alterações não staged"]
            )
        ),
        impact_fn=_restore_impact,
        verify_fn=lambda a, p, c, after: (
            _fail("ainda há alterações")
            if (not a.get("source") and _matching(a["paths"], after.unstaged))
            else _ok("arquivos restaurados")
        ),
    )
)
_register(
    Operation(
        "rm",
        "Remover arquivos do repositório (ou só parar de rastrear)",
        {"paths": P("paths", True), "cached": P("bool", default=False)},
        lambda a, c: [
            "rm",
            *_flag(a.get("cached"), "--cached"),
            *_flag(any((c.repo.root / p).is_dir() for p in a["paths"]), "-r"),
            "--",
            *a["paths"],
        ],
        risk=Risk.MODERATE,
        mutating=True,
        impact_fn=lambda a, c: Impact(
            ["o arquivo deixa de ser rastreado, mas continua no disco"]
            if a.get("cached")
            else [
                "remove o arquivo do disco e do índice (o Git recusa se houver alterações locais)"
            ],
            "git restore --staged --worktree --source=HEAD <arquivo>",
            files=a["paths"],
        ),
    )
)
_register(
    Operation(
        "mv",
        "Mover/renomear arquivo rastreado",
        {"source": P("path", True), "destination": P("path", True)},
        lambda a, c: ["mv", "--", a["source"], a["destination"]],
        risk=Risk.MODERATE,
        mutating=True,
        check_fn=lambda a, c: (
            [] if (c.repo.root / a["source"]).exists() else [f"arquivo inexistente: {a['source']}"]
        ),
        impact_fn=lambda a, c: Impact(
            [f"move {a['source']} para {a['destination']}"],
            f"git mv {a['destination']} {a['source']}",
            files=[a["source"], a["destination"]],
        ),
    )
)


def _clean_pre(a: Args, c: OpContext) -> Pre:
    return {
        "files": c.reader.clean_preview(
            bool(a.get("directories")), bool(a.get("ignored")), a.get("paths", [])
        )
    }


_register(
    Operation(
        "clean",
        "Apagar arquivos não rastreados",
        {
            "directories": P("bool", default=True),
            "ignored": P("bool", default=False),
            "paths": P("paths"),
        },
        lambda a, c: [
            "clean",
            "-f",
            *_flag(a.get("directories"), "-d"),
            *_flag(a.get("ignored"), "-x"),
            "--",
            *a.get("paths", []),
        ],
        risk=Risk.HIGH,
        mutating=True,
        check_fn=lambda a, c: [] if _clean_pre(a, c)["files"] else ["nada para limpar"],
        impact_fn=lambda a, c: Impact(
            ["APAGA do disco os arquivos não rastreados listados"]
            + (
                ["inclui arquivos ignorados (.gitignore), como builds e .env"]
                if a.get("ignored")
                else []
            ),
            "não há recuperação pelo Git",
            files=_clean_pre(a, c)["files"],
        ),
        pre_fn=_clean_pre,
        verify_fn=lambda a, p, c, after: (
            _fail("arquivos ainda existem")
            if any((c.repo.root / f).exists() for f in p["files"])
            else _ok(f"{len(p['files'])} removido(s)")
        ),
    )
)


# --------------------------------------------------------------------------- tags & remotes

_register(
    Operation(
        "tag_create",
        "Criar tag",
        {"name": P("refname", True), "rev": P("rev", default="HEAD"), "message": P("message")},
        lambda a, c: [
            "tag",
            *(["--annotate", f"--message={a['message']}"] if a.get("message") else []),
            a["name"],
            a["rev"],
        ],
        risk=Risk.MODERATE,
        mutating=True,
        check_fn=lambda a, c: (
            ([f"a tag {a['name']} já existe"] if c.reader.tag_exists(a["name"]) else [])
            + _rev_errors(c, a["rev"])
        ),
        impact_fn=lambda a, c: Impact(
            [f"cria a tag {a['name']} em {a['rev']}"], f"git tag --delete {a['name']}"
        ),
        verify_fn=lambda a, p, c, after: (
            _ok("tag criada") if c.reader.tag_exists(a["name"]) else _fail("tag não encontrada")
        ),
    )
)
_register(
    Operation(
        "tag_delete",
        "Excluir tag local",
        {"name": P("refname", True)},
        lambda a, c: ["tag", "--delete", a["name"]],
        risk=Risk.MODERATE,
        mutating=True,
        check_fn=lambda a, c: (
            [] if c.reader.tag_exists(a["name"]) else [f"tag inexistente: {a['name']}"]
        ),
        impact_fn=lambda a, c: Impact(
            [f"exclui a tag local {a['name']}"],
            f"git tag {a['name']} {c.reader.resolve(a['name']) or '<sha>'}",
        ),
        verify_fn=lambda a, p, c, after: (
            _fail("tag ainda existe") if c.reader.tag_exists(a["name"]) else _ok("tag excluída")
        ),
    )
)
_register(
    Operation(
        "remote_add",
        "Adicionar remoto",
        {"name": P("remote", True), "url": P("url", True)},
        lambda a, c: ["remote", "add", a["name"], a["url"]],
        risk=Risk.MODERATE,
        mutating=True,
        check_fn=lambda a, c: (
            [f"remoto já existe: {a['name']}"] if a["name"] in c.reader.remotes() else []
        ),
        impact_fn=lambda a, c: Impact(
            [f"adiciona o remoto {a['name']} → {a['url']}"], f"git remote remove {a['name']}"
        ),
    )
)
_register(
    Operation(
        "remote_remove",
        "Remover remoto",
        {"name": P("remote", True)},
        lambda a, c: ["remote", "remove", a["name"]],
        risk=Risk.MODERATE,
        mutating=True,
        check_fn=lambda a, c: (
            [] if a["name"] in c.reader.remotes() else [f"remoto inexistente: {a['name']}"]
        ),
        impact_fn=lambda a, c: Impact(
            [
                f"remove o remoto {a['name']} e suas referências remotas locais",
                "nada é apagado no servidor",
            ],
            f"git remote add {a['name']} {c.reader.remote_url(a['name']) or '<url>'}",
        ),
    )
)
_register(
    Operation(
        "remote_rename",
        "Renomear remoto",
        {"old": P("remote", True), "new": P("remote", True)},
        lambda a, c: ["remote", "rename", a["old"], a["new"]],
        risk=Risk.MODERATE,
        mutating=True,
        check_fn=lambda a, c: (
            [] if a["old"] in c.reader.remotes() else [f"remoto inexistente: {a['old']}"]
        ),
        impact_fn=lambda a, c: Impact(
            [f"renomeia o remoto {a['old']} para {a['new']}"],
            f"git remote rename {a['new']} {a['old']}",
        ),
    )
)
_register(
    Operation(
        "remote_set_url",
        "Alterar URL de um remoto",
        {"name": P("remote", True), "url": P("url", True)},
        lambda a, c: ["remote", "set-url", a["name"], a["url"]],
        risk=Risk.MODERATE,
        mutating=True,
        check_fn=lambda a, c: (
            [] if a["name"] in c.reader.remotes() else [f"remoto inexistente: {a['name']}"]
        ),
        impact_fn=lambda a, c: Impact(
            [f"{a['name']} passa a apontar para {a['url']}"],
            f"git remote set-url {a['name']} {c.reader.remote_url(a['name']) or '<url antiga>'}",
        ),
    )
)


def catalog_for_prompt() -> str:
    """One line per operation, for the LLM system prompt."""
    lines = []
    for op in CATALOG.values():
        params = ", ".join(
            f"{name}{'' if p.required else '?'}:{p.kind}"
            + (f"[{'|'.join(p.choices)}]" if p.choices else "")
            for name, p in op.params.items()
        )
        lines.append(f"- {op.name}({params}): {op.summary}")
    return "\n".join(lines)
