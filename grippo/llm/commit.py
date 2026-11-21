"""Choose the files related to a commit request and draft a Conventional Commit message.

Selection strategy (measured on qwen2.5:3b Q4_K_M, see docs/LLM.md):

1. generic request ("faça commit") → deterministic: tracked changes (+ untracked only when
   there is nothing tracked, or when the user says "tudo"/"all");
2. topical request → union of
   * deterministic keyword matches between the request and the paths (high precision),
   * one small yes/no LLM decision per file (handles pt/en synonyms),
   * files sharing a stem with a selected file (``signup_form.html`` ↔ ``signup_form.js``);
3. the message is drafted by a separate LLM call that sees only the selected files.

The LLM proposes; this module enforces that files are a subset of the real changes,
sensitive files are never selected and the message is a valid Conventional Commit.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from grippo.agent.router import fold
from grippo.errors import LLMError
from grippo.git.reader import GitReader
from grippo.git.state import RepoState
from grippo.git.validate import is_sensitive_path
from grippo.llm.client import LLMMetrics, OllamaClient
from grippo.llm.prompts import MESSAGE_SCHEMA, RELEVANCE_SCHEMA, message_prompt, relevance_prompt

log = logging.getLogger(__name__)

CONVENTIONAL = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([\w\-./ ]+\))?!?: \S.{1,}$"
)
_DOC_SUFFIXES = {".md", ".rst", ".txt", ".adoc"}
_PREVIEW_BYTES = 400
MAX_LLM_RELEVANCE_FILES = 30
_STOPWORDS = frozenset(
    [
        "commit",
        "commita",
        "commitar",
        "comitar",
        "commite",
        "comite",
        "commits",
        "faca",
        "faz",
        "fazer",
        "fazendo",
        "quero",
        "queria",
        "preciso",
        "pode",
        "poderia",
        "favor",
        "por",
        "para",
        "pra",
        "com",
        "que",
        "uma",
        "umas",
        "uns",
        "das",
        "dos",
        "the",
        "and",
        "for",
        "this",
        "that",
        "alteracoes",
        "alteracao",
        "mudancas",
        "mudanca",
        "modificacoes",
        "changes",
        "change",
        "arquivos",
        "arquivo",
        "files",
        "file",
        "minhas",
        "meus",
        "minha",
        "meu",
        "nova",
        "novo",
        "novas",
        "novos",
        "new",
        "isso",
        "isto",
        "esse",
        "essa",
        "este",
        "esta",
        "aqui",
        "agora",
        "atual",
        "atuais",
        "ultimas",
        "ultimos",
        "all",
        "tudo",
        "todos",
        "todas",
        "everything",
        "please",
        "make",
        "create",
        "crie",
        "criar",
        "cria",
        "prepara",
        "prepare",
        "preparar",
        "push",
        "envie",
        "enviar",
        "envia",
        "remoto",
        "origin",
        "git",
        "add",
        "foram",
        "feitas",
        "feitos",
        "feito",
        "feita",
        "realizadas",
        "realizados",
    ]
)
_ALL_WORDS = frozenset({"tudo", "todos", "todas", "all", "everything"})


@dataclass
class CommitDraft:
    files: list[str]
    message: str
    unrelated: list[str] = field(default_factory=list)
    excluded_sensitive: list[str] = field(default_factory=list)
    source: str = "heuristic"
    metrics: LLMMetrics | None = None
    notes: list[str] = field(default_factory=list)


def normalize_message(message: str) -> str | None:
    subject = message.strip().strip("`\"'").splitlines()[0].strip() if message.strip() else ""
    if not subject:
        return None
    if len(subject) > 72:
        subject = subject[:72].rstrip()
    return subject if CONVENTIONAL.match(subject) else None


def _raw_words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", fold(text)))


def _words(text: str) -> set[str]:
    return {w for w in _raw_words(text) if w not in _STOPWORDS}


def _path_words(path: str) -> set[str]:
    return _raw_words(re.sub(r"[/_.\-]", " ", path))


# Portuguese request words → English identifiers commonly found in paths.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "formulario": ("form", "forms"),
    "cadastro": ("signup", "register", "registration"),
    "usuario": ("user", "users"),
    "usuarios": ("user", "users"),
    "senha": ("password",),
    "autenticacao": ("auth", "login"),
    "pagamento": ("payment", "payments", "billing"),
    "fatura": ("invoice", "billing"),
    "faturamento": ("billing", "invoice"),
    "teste": ("test", "tests", "spec"),
    "testes": ("test", "tests", "spec"),
    "documentacao": ("docs", "doc", "readme"),
    "configuracao": ("config", "settings"),
    "tela": ("screen", "page", "view"),
    "pagina": ("page", "view"),
    "botao": ("button",),
    "banco": ("db", "database"),
    "rota": ("route", "routes", "router"),
    "estilo": ("style", "styles", "css"),
    "imagem": ("image", "img"),
    "busca": ("search",),
    "carrinho": ("cart",),
    "produto": ("product", "products"),
    "pedido": ("order", "orders"),
    "relatorio": ("report", "reports"),
    "cliente": ("client", "customer"),
    "limpeza": ("clean", "cleanup"),
    "script": ("scripts",),
    "traducao": ("i18n", "locale"),
}


def _related_by_words(request_words: set[str], path: str) -> bool:
    """Exact word, shared prefix (>= 4 chars) or known pt→en synonym."""
    path_words = _path_words(path)
    if request_words & path_words:
        return True
    for word in request_words:
        if any(syn in path_words for syn in _SYNONYMS.get(word, ())):
            return True
        for candidate in path_words:
            shortest = min(len(word), len(candidate))
            if shortest >= 4 and (word.startswith(candidate) or candidate.startswith(word)):
                return True
    return False


def _stem(path: str) -> tuple[str, str]:
    pure = PurePosixPath(path.rstrip("/"))
    return str(pure.parent), pure.name.split(".")[0]


def _scope(files: list[str]) -> str | None:
    tops = {PurePosixPath(f).parts[0] for f in files if len(PurePosixPath(f).parts) > 1}
    if len(tops) == 1:
        scope = tops.pop()
        return None if scope in {"src", "app", "lib"} else re.sub(r"[^\w\-.]", "", scope)
    return None


def _added(state: RepoState) -> set[str]:
    return {e.path for e in state.entries if e.index == "A"} | set(state.untracked)


def heuristic_message(files: list[str], state: RepoState, request: str = "") -> str:
    added = _added(state)
    suffixes = {PurePosixPath(f).suffix for f in files}
    names = [PurePosixPath(f.rstrip("/")).name for f in files]
    all_new = bool(files) and all(f in added for f in files)
    if files and suffixes <= _DOC_SUFFIXES:
        kind = "docs"
    elif files and all("test" in f.lower() for f in files):
        kind = "test"
    elif files and all(f.startswith((".github/", ".gitlab", "ci/")) for f in files):
        kind = "ci"
    elif any(f in added for f in files):
        kind = "feat"
    else:
        kind = "chore"
    scope = _scope(files)
    verb = "add" if all_new else "update"
    topic = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    head = f"{kind}({scope})" if scope else kind
    return f"{head}: {verb} {topic}"[:72].rstrip()


def _generic_selection(request: str, candidates: list[str], state: RepoState) -> list[str]:
    if _raw_words(request) & _ALL_WORDS:
        return list(candidates)
    tracked = [c for c in candidates if c not in state.untracked]
    return tracked or list(candidates)


def _file_table(candidates: list[str], state: RepoState, reader: GitReader) -> str:
    numstat = reader.numstat(staged=False)
    numstat.update(reader.numstat(staged=True))
    labels = {e.path: e.label for e in state.entries}
    rows = []
    for path in candidates[:60]:
        label = labels.get(path, "novo (não rastreado)")
        added, removed = numstat.get(path, (0, 0))
        rows.append(f"- {path} [{label}] +{added} -{removed}")
    return "\n".join(rows)


def _file_preview(root: Path, path: str, state: RepoState, reader: GitReader, staged: bool) -> str:
    if path in state.untracked:
        target = root / path
        if not target.is_file() or is_sensitive_path(path):
            return "(diretório ou arquivo não legível)"
        try:
            data = target.read_bytes()[:_PREVIEW_BYTES]
        except OSError:
            return "(não legível)"
        return "(binário)" if b"\0" in data else data.decode("utf-8", errors="replace")
    return reader.diff_text([path], staged=staged, max_chars=_PREVIEW_BYTES) or "(sem diff)"


def _diff_excerpt(
    root: Path, files: list[str], state: RepoState, reader: GitReader, staged: bool, budget: int
) -> str:
    tracked = [f for f in files if f not in state.untracked]
    text = reader.diff_text(tracked, staged=staged, max_chars=budget) if tracked else ""
    for path in files:
        if path in state.untracked and len(text) < budget:
            preview = _file_preview(root, path, state, reader, staged)
            text += f"\n+++ {path} (novo)\n{preview}"
    return text[:budget]


class CommitDrafter:
    def __init__(self, llm: OllamaClient | None, max_diff_chars: int = 6000) -> None:
        self.llm = llm
        self.max_diff_chars = max_diff_chars

    # ------------------------------------------------------------------ public
    def draft(
        self,
        request: str,
        state: RepoState,
        reader: GitReader,
        candidates: list[str],
        *,
        staged_only: bool = False,
    ) -> CommitDraft:
        sensitive = [c for c in candidates if is_sensitive_path(c)]
        candidates = [c for c in candidates if c not in sensitive]
        if not candidates:
            return CommitDraft([], "", excluded_sensitive=sensitive)
        notes: list[str] = []
        metrics: list[LLMMetrics] = []
        if staged_only:
            files = list(candidates)
        else:
            files = self._select(request, candidates, state, reader, notes, metrics)
            if not files:
                return CommitDraft(
                    [], "", list(candidates), sensitive, "none", _merge(metrics), notes
                )
        message, source = self._message(request, files, state, reader, staged_only, notes, metrics)
        return CommitDraft(
            files=files,
            message=message,
            unrelated=[c for c in candidates if c not in files],
            excluded_sensitive=sensitive,
            source=source,
            metrics=_merge(metrics),
            notes=notes,
        )

    # ------------------------------------------------------------------ selection
    def _select(
        self,
        request: str,
        candidates: list[str],
        state: RepoState,
        reader: GitReader,
        notes: list[str],
        metrics: list[LLMMetrics],
    ) -> list[str]:
        # The project's own name ("commit do grippo") means "this repo", not a topic.
        words = _words(request) - _raw_words(reader.repo.name)
        if not words:
            return _generic_selection(request, candidates, state)
        chosen = {c for c in candidates if _related_by_words(words, c)}
        if self.llm is not None and len(candidates) <= MAX_LLM_RELEVANCE_FILES:
            try:
                for path in candidates:
                    if path in chosen:
                        continue
                    preview = _file_preview(reader.repo.root, path, state, reader, False)
                    data, reply = self.llm.chat_json(
                        relevance_prompt(request, path, preview), RELEVANCE_SCHEMA, max_tokens=12
                    )
                    metrics.append(reply.metrics)
                    if data.get("related") is True:
                        chosen.add(path)
            except LLMError as error:
                notes.append(f"LLM indisponível na seleção ({error}); usando palavras-chave")
        elif self.llm is not None:
            notes.append(f"muitos arquivos ({len(candidates)}): seleção só por palavras-chave")
        if chosen:
            stems = {_stem(c) for c in chosen}
            chosen |= {c for c in candidates if _stem(c) in stems}
            return [c for c in candidates if c in chosen]
        notes.append("não identifiquei arquivos ligados ao pedido")
        # Never fall back to "everything" for a specific request: the planner asks instead.
        return []

    # ------------------------------------------------------------------ message
    def _message(
        self,
        request: str,
        files: list[str],
        state: RepoState,
        reader: GitReader,
        staged_only: bool,
        notes: list[str],
        metrics: list[LLMMetrics],
    ) -> tuple[str, str]:
        fallback = heuristic_message(files, state, request)
        if self.llm is None:
            return fallback, "heuristic"
        diff = _diff_excerpt(
            reader.repo.root, files, state, reader, staged_only, self.max_diff_chars
        )
        try:
            data, reply = self.llm.chat_json(
                message_prompt(request, _file_table(files, state, reader), diff),
                MESSAGE_SCHEMA,
                max_tokens=60,
            )
        except LLMError as error:
            log.warning("mensagem via LLM falhou: %s", error)
            notes.append(f"LLM indisponível ({error}); mensagem heurística")
            return fallback, "heuristic"
        metrics.append(reply.metrics)
        message = normalize_message(str(data.get("message") or ""))
        if message is None:
            notes.append(f"mensagem do modelo inválida ({data.get('message')!r}); heurística")
            return fallback, "heuristic"
        return message, "llm"


def _merge(metrics: list[LLMMetrics]) -> LLMMetrics | None:
    if not metrics:
        return None
    return LLMMetrics(
        wall_sec=sum(m.wall_sec for m in metrics),
        load_sec=sum(m.load_sec for m in metrics),
        prompt_tokens=sum(m.prompt_tokens for m in metrics),
        prompt_sec=sum(m.prompt_sec for m in metrics),
        output_tokens=sum(m.output_tokens for m in metrics),
        output_sec=sum(m.output_sec for m in metrics),
    )
