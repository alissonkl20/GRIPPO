"""Explain *why* a merge/rebase/cherry-pick/revert stopped with conflicts.

Read-only and deterministic: it collects the facts (which side changed which file, in
which commits, and what the conflicting hunks look like). It never proposes a resolution —
deciding which side to keep is the user's call. :func:`render` turns the facts into text,
and :func:`llm_summary` optionally asks the local model for a short, explanation-only recap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from grippo.errors import LLMError
from grippo.git.reader import GitReader
from grippo.git.state import FileEntry, RepoState
from grippo.llm.client import OllamaClient

MAX_FILES = 8
MAX_HUNKS = 3
MAX_HUNK_LINES = 6
_OTHER_HEAD = {
    "merge": "MERGE_HEAD",
    "cherry-pick": "CHERRY_PICK_HEAD",
    "revert": "REVERT_HEAD",
    "rebase": "REBASE_HEAD",
    "rebase-merge": "REBASE_HEAD",
    "rebase-apply": "REBASE_HEAD",
}
_KINDS = {
    ("U", "U"): "os dois lados alteraram as mesmas linhas",
    ("A", "A"): "os dois lados criaram este arquivo com conteúdos diferentes",
    ("D", "D"): "os dois lados removeram o arquivo",
    ("D", "U"): "um lado removeu o arquivo e o outro o alterou",
    ("U", "D"): "um lado alterou o arquivo e o outro o removeu",
    ("A", "U"): "um lado criou o arquivo e o outro criou/alterou com outro conteúdo",
    ("U", "A"): "um lado criou o arquivo e o outro criou/alterou com outro conteúdo",
}
_COMMAND_WORD = {
    "rebase-merge": "rebase",
    "rebase-apply": "rebase",
}
_MARKER = re.compile(r"^(<{7}|={7}|>{7})(?: (.*))?$")


@dataclass
class Hunk:
    ours: list[str]
    theirs: list[str]


@dataclass
class FileConflict:
    path: str
    kind: str
    our_commits: list[str] = field(default_factory=list)
    their_commits: list[str] = field(default_factory=list)
    hunks: list[Hunk] = field(default_factory=list)
    hunk_total: int = 0


@dataclass
class ConflictReport:
    operation: str
    our_label: str
    their_label: str
    base: str | None
    files: list[FileConflict]
    omitted: int = 0


def parse_hunks(text: str) -> list[Hunk]:
    hunks: list[Hunk] = []
    ours: list[str] = []
    theirs: list[str] = []
    section = ""
    for line in text.splitlines():
        marker = _MARKER.match(line)
        if marker and marker.group(1) == "<<<<<<<":
            section, ours, theirs = "ours", [], []
        elif marker and marker.group(1) == "=======" and section == "ours":
            section = "theirs"
        elif marker and marker.group(1) == ">>>>>>>" and section == "theirs":
            hunks.append(Hunk(ours, theirs))
            section = ""
        elif section == "ours":
            ours.append(line)
        elif section == "theirs":
            theirs.append(line)
    return hunks


def _label(reader: GitReader, rev: str, fallback: str) -> str:
    sha = reader.resolve(rev)
    if not sha:
        return fallback
    names = reader.run(
        [
            "for-each-ref",
            "--points-at",
            sha,
            "--format=%(refname:short)",
            "refs/heads",
            "refs/remotes",
        ]
    )
    branch = names.stdout.split()[0] if names.ok and names.stdout.split() else ""
    subject = reader.commits(sha, limit=1)
    text = subject[0].subject if subject else ""
    return f"{branch or sha[:8]} ({sha[:8]} {text})".strip()


def _commits_touching(reader: GitReader, spec: str, path: str) -> list[str]:
    result = reader.run(["log", "-n5", "--format=%h %s", spec, "--", path])
    return result.stdout.splitlines() if result.ok else []


def _kind(entry: FileEntry) -> str:
    return _KINDS.get((entry.index, entry.worktree), "alterações incompatíveis entre os lados")


def collect(reader: GitReader, state: RepoState) -> ConflictReport | None:
    if not state.conflicted:
        return None
    operation = state.in_progress or "merge"
    other_head = _OTHER_HEAD.get(operation, "MERGE_HEAD")
    other = reader.resolve(other_head)
    base_result = reader.run(["merge-base", "HEAD", other_head]) if other else None
    base = base_result.stdout.strip() if base_result and base_result.ok else None
    rebasing = operation.startswith("rebase")
    our_label = _label(reader, "HEAD", "HEAD")
    their_label = _label(reader, other_head, other_head) if other else "(desconhecido)"
    files = []
    entries = [e for e in state.entries if e.conflicted]
    for entry in entries[:MAX_FILES]:
        conflict = FileConflict(entry.path, _kind(entry))
        if base and other:
            conflict.our_commits = _commits_touching(reader, f"{base}..HEAD", entry.path)
            conflict.their_commits = _commits_touching(reader, f"{base}..{other_head}", entry.path)
        try:
            text = (Path(state.root) / entry.path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        found = parse_hunks(text)
        conflict.hunk_total = len(found)
        conflict.hunks = found[:MAX_HUNKS]
        files.append(conflict)
    if rebasing:
        # During a rebase "ours" is the branch being rebuilt onto the new base.
        our_label, their_label = f"{our_label} [base nova]", f"{their_label} [seu commit]"
    return ConflictReport(
        operation, our_label, their_label, base, files, max(len(entries) - MAX_FILES, 0)
    )


def _clip(lines: list[str]) -> list[str]:
    shown = [f"    {line[:100]}" for line in lines[:MAX_HUNK_LINES]]
    if len(lines) > MAX_HUNK_LINES:
        shown.append(f"    … (+{len(lines) - MAX_HUNK_LINES} linhas)")
    return shown or ["    (vazio)"]


def render(report: ConflictReport) -> str:
    out = [
        f"Conflito durante {report.operation} em {len(report.files) + report.omitted} arquivo(s).",
        f"  lado A (atual): {report.our_label}",
        f"  lado B (outro): {report.their_label}",
        "",
        "Por que aconteceu: os dois lados mexeram nos mesmos arquivos, de forma incompatível, "
        "a partir do ancestral comum"
        + (f" ({report.base[:8]})" if report.base else "")
        + "; o Git não sabe qual versão manter (detalhes por arquivo abaixo).",
    ]
    for item in report.files:
        out += ["", f"■ {item.path} — {item.kind}"]
        for title, commits in (("A", item.our_commits), ("B", item.their_commits)):
            if commits:
                out.append(f"  lado {title} mudou em: " + "; ".join(commits[:3]))
        for number, hunk in enumerate(item.hunks, 1):
            out.append(f"  trecho {number}/{item.hunk_total}:")
            out += ["   A:", *_clip(hunk.ours), "   B:", *_clip(hunk.theirs)]
    if report.omitted:
        out.append(f"\n… e mais {report.omitted} arquivo(s) em conflito.")
    command = _COMMAND_WORD.get(report.operation, report.operation)
    out += [
        "",
        "O GRIPPO não escolhe o lado por você. Edite os arquivos (ou peça ao seu assistente "
        f'de código), depois diga "continue o {command}" — ou "aborte o {command}" '
        "para voltar ao estado anterior.",
    ]
    return "\n".join(out)


SYSTEM = (
    "Você explica conflitos de Git em português do Brasil. Responda em NO MÁXIMO 4 frases "
    "curtas dizendo APENAS POR QUE o conflito ocorreu: o que cada lado mudou e por que "
    "isso colide. É PROIBIDO sugerir solução, dizer qual lado manter, escrever código "
    "ou comandos. Use somente os fatos fornecidos; não invente nada."
)


def llm_summary(client: OllamaClient, report: ConflictReport) -> str | None:
    """Short explanation-only recap from the local model.

    ``None`` when the model fails or strays into giving instructions (code, commands).
    """
    try:
        reply = client.chat(
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": render(report)[:3000]},
            ],
            max_tokens=200,
        )
    except LLMError:
        return None
    text = reply.text.strip()
    if not text or "`" in text or re.search(r"\bgit [a-z]", text):
        return None
    return text
