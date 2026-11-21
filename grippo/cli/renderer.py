"""Terminal rendering (rich). No business logic here."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console, Group, RenderableType
from rich.markup import escape
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from grippo import __version__
from grippo.agent.assistant import TurnResult
from grippo.cli.theme import THEME
from grippo.engine.operations import Risk
from grippo.engine.plan import PlanResult, PreparedPlan, StepStatus
from grippo.git.state import RepoState

console = Console(highlight=False, theme=THEME)

_RISK = {
    Risk.LOW: ("risk.low", "border.low", "BAIXO"),
    Risk.MODERATE: ("risk.moderate", "border.moderate", "MODERADO"),
    Risk.HIGH: ("risk.high", "border.high", "ALTO"),
}
_STATUS_ICON = {
    StepStatus.DONE: "[ok]✔[/]",
    StepStatus.FAILED: "[err]✘[/]",
    StepStatus.CONFLICT: "[warn]⚠[/]",
    StepStatus.INTERRUPTED: "[warn]■[/]",
    StepStatus.TIMEOUT: "[err]⏱[/]",
    StepStatus.SKIPPED: "[muted]–[/]",
    StepStatus.REJECTED: "[err]⊘[/]",
}
_MAX_OUTPUT_LINES = 80
_MAX_LISTED = 12


def show_header(*, animate: bool = True, cycles: int = 2) -> None:
    """Cheetah header (animated on a real terminal; see grippo/cli/splash.py)."""
    from grippo.cli import splash

    splash.show(console.file, __version__, animate=animate, cycles=cycles)


def risk_badge(risk: Risk) -> str:
    style, _, label = _RISK[risk]
    return f"[{style}] {label} [/]"


def _grid() -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="label", no_wrap=True)
    table.add_column()
    return table


def _branch_line(state: RepoState) -> str:
    line = f"[branch]{escape(state.branch or '(HEAD destacado)')}[/]"
    if state.upstream:
        ahead = f"[ok]↑{state.ahead}[/]" if state.ahead else "[muted]↑0[/]"
        behind = f"[warn]↓{state.behind}[/]" if state.behind else "[muted]↓0[/]"
        line += f" [muted]→[/] [path]{escape(state.upstream)}[/] {ahead} {behind}"
    else:
        line += " [muted](sem upstream)[/]"
    return line


def _head_line(state: RepoState) -> str:
    return f"[sha]{state.short_head}[/] {escape(state.head_subject or '')}"


def _tree_counts(state: RepoState) -> str:
    if state.clean:
        line = "[ok]✔ working tree limpo[/]"
    else:
        parts = []
        if state.conflicted:
            parts.append(f"[err]{len(state.conflicted)} em conflito[/]")
        if state.staged:
            parts.append(f"[ok]{len(state.staged)} staged[/]")
        if state.unstaged:
            parts.append(f"[warn]{len(state.unstaged)} não staged[/]")
        if state.untracked:
            parts.append(f"[err]{len(state.untracked)} não rastreados[/]")
        line = " [muted]·[/] ".join(parts)
    if state.in_progress:
        line += f"  [risk.moderate] {escape(state.in_progress)} em andamento [/]"
    return line


def show_welcome(
    repo_root: Path, state: RepoState | None, llm_label: str, autonomy: str = ""
) -> None:
    table = _grid()
    table.add_row("repositório", f"[path]{escape(str(repo_root))}[/]")
    if state is not None:
        table.add_row("branch", _branch_line(state))
        table.add_row("HEAD", _head_line(state))
        table.add_row("alterações", _tree_counts(state))
    dot = "[muted]○[/]" if "desativada" in llm_label else "[ok]●[/]"
    table.add_row("LLM", f"{dot} {escape(llm_label)}")
    if autonomy:
        table.add_row("autonomia", f"[accent]{escape(autonomy)}[/]")
    hint = Text.from_markup(
        "[muted]Fale em linguagem natural ·[/] [brand]/help[/] [muted]comandos · "
        "Para sair:[/] [brand]exit[/][muted], Ctrl-D ou Ctrl-C duas vezes.[/]"
    )
    console.print(
        Panel(
            Group(table, Text(""), hint),
            border_style="border.brand",
            padding=(0, 1),
            title="[brand] GRIPPO [/]",
            title_align="left",
        )
    )


def show_error(message: str) -> None:
    console.print(f"[err]✘ Erro:[/] {escape(message)}")


def show_info(message: str, style: str = "") -> None:
    console.print(f"[{style}]{escape(message)}[/]" if style else escape(message))


def show_notes(notes: list[str]) -> None:
    for note in notes:
        console.print(f"[muted]  · {escape(note)}[/]")


def _file_list(paths: list[str], style: str, limit: int = _MAX_LISTED) -> str:
    shown = ", ".join(f"[{style}]{escape(p)}[/]" for p in paths[:limit])
    if len(paths) > limit:
        shown += f" [muted]+{len(paths) - limit}[/]"
    return shown


def show_state(state: RepoState) -> None:
    table = _grid()
    table.add_row("branch", _branch_line(state))
    table.add_row("HEAD", _head_line(state))
    if state.in_progress:
        table.add_row("andamento", f"[risk.moderate] {escape(state.in_progress)} [/]")
    if state.stash_count:
        table.add_row("stashes", f"[accent]{state.stash_count}[/]")
    if state.clean:
        table.add_row("working tree", "[ok]✔ limpo[/]")
    for label, items, style in (
        ("conflitos", state.conflicted, "err"),
        ("staged", state.staged, "ok"),
        ("não staged", state.unstaged, "warn"),
        ("não rastreados", list(state.untracked), "err"),
    ):
        if items:
            table.add_row(f"{label} [{style}]{len(items)}[/]", _file_list(items, "path"))
    console.print(
        Panel(
            table,
            title="[accent] estado [/]",
            title_align="left",
            border_style="border.muted",
            padding=(0, 1),
        )
    )


def _bullets(items: list[str], style: str = "") -> list[str]:
    if style:
        return [f"    [muted]•[/] [{style}]{escape(i)}[/]" for i in items]
    return [f"    [muted]•[/] {escape(i)}" for i in items]


def show_prepared(
    prepared: PreparedPlan, *, details: bool = False, warnings: list[str] | None = None
) -> None:
    state = prepared.state
    _, border, _ = _RISK[prepared.risk]
    head = _grid()
    head.add_row("repositório", f"[path]{escape(prepared.repo_root)}[/]")
    head.add_row("branch", f"{_branch_line(state)}  [label]HEAD[/] [sha]{state.short_head}[/]")
    head.add_row("working tree", _tree_counts(state))
    head.add_row("risco", risk_badge(prepared.risk))
    body: list[RenderableType] = [head]
    for index, preview in enumerate(prepared.previews, start=1):
        lines = [
            f"[brand]{index}[/] [bold]{escape(preview.step.op)}[/]  {risk_badge(preview.risk)}"
        ]
        args = {k: v for k, v in preview.step.args.items() if v not in (False, None, [], "")}
        if preview.impact.files:
            args.pop("paths", None)  # listed below as "arquivos"
        if args:
            lines.append(
                "    "
                + "  ".join(
                    f"[label]{escape(k)}=[/][accent]{escape(str(v))}[/]" for k, v in args.items()
                )
            )
        if details or prepared.risk is Risk.HIGH:
            lines.append(f"    [label]comando:[/] [muted]git {escape(' '.join(preview.argv))}[/]")
        impact = preview.impact
        lines.extend(_bullets(impact.consequences))
        if impact.files:
            shown = impact.files if details else impact.files[:_MAX_LISTED]
            lines.append(f"    [label]arquivos ({len(impact.files)}):[/]")
            lines.extend(_bullets(shown, "path"))
            if len(impact.files) > len(shown):
                lines.append(f"    [muted]… +{len(impact.files) - len(shown)} (d mostra todos)[/]")
        if impact.commits:
            lines.append("    [label]commits:[/]")
            lines.extend(_bullets(impact.commits, "commit"))
        lines.extend(f"    [warn]⚠ {escape(w)}[/]" for w in impact.warnings)
        if impact.recovery:
            lines.append(f"    [label]recuperação:[/] [muted italic]{escape(impact.recovery)}[/]")
        lines.extend(f"    [err]✘ {escape(e)}[/]" for e in preview.errors)
        body.append(Padding(Text.from_markup("\n".join(lines)), (1, 0, 0, 0)))
    extra = (warnings or []) + list(prepared.plan.warnings)
    if extra:
        body.append(
            Padding(
                Text.from_markup("\n".join(f"[warn]⚠ {escape(w)}[/]" for w in extra)), (1, 0, 0, 0)
            )
        )
    console.print(
        Panel(
            Group(*body),
            title="[bold] operação proposta [/]",
            title_align="left",
            border_style=border,
            padding=(0, 1),
        )
    )


def _trim(text: str) -> str:
    lines = text.rstrip().splitlines()
    if len(lines) > _MAX_OUTPUT_LINES:
        omitted = len(lines) - _MAX_OUTPUT_LINES
        lines = [*lines[:_MAX_OUTPUT_LINES], f"… ({omitted} linhas omitidas)"]
    return "\n".join(lines)


_HEX = frozenset("0123456789abcdef")


def colorize_output(text: str) -> Text:
    """Light highlighting of git output: diff +/- lines, hunks, short hashes."""
    out = Text()
    for line in _trim(text).splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            out.append(line, style="green")
        elif line.startswith("-") and not line.startswith("---"):
            out.append(line, style="red")
        elif line.startswith("@@"):
            out.append(line, style="cyan")
        elif line.startswith(("diff --git", "commit ")):
            out.append(line, style="bold color(214)")
        elif len(line) > 8 and line[7] == " " and set(line[:7]) <= _HEX:
            out.append(line[:7], style="color(220)")
            out.append(line[7:])
        else:
            out.append(line)
        out.append("\n")
    out.rstrip()
    return out


def show_result(result: PlanResult, *, show_output: bool) -> None:
    for item in result.results:
        line = f"{_STATUS_ICON[item.status]} [bold]{escape(item.step.op)}[/]"
        if item.command:
            line += f"  [muted]{escape(item.command)}[/]"
        console.print(line)
        if item.verification and item.status is not StepStatus.SKIPPED:
            style = "ok" if item.verification.ok else "err"
            console.print(f"   [{style}]{escape(item.verification.message)}[/]")
        if show_output and item.stdout.strip():
            console.print(Padding(colorize_output(item.stdout), (0, 0, 0, 3)))
        if item.status is not StepStatus.DONE and item.stderr.strip():
            console.print(Padding(Text(_trim(item.stderr), style="muted"), (0, 0, 0, 3)))
        if item.hint:
            console.print(f"   [warn]→ {escape(item.hint)}[/]")
    if result.ok:
        console.print(f"[ok]✔ {escape(result.message)}[/]")
    elif result.status in (StepStatus.CONFLICT, StepStatus.INTERRUPTED):
        console.print(f"[warn]⚠ {escape(result.message)}[/]")
    else:
        console.print(f"[err]✘ {escape(result.message)}[/]")


def show_turn(turn: TurnResult) -> None:
    show_notes(turn.notes)
    if turn.kind in {"answer", "noop"}:
        console.print(f"[brand]◆[/] {escape(turn.message)}")
    elif turn.kind == "clarify":
        console.print(f"[accent]?[/] {escape(turn.message)}")
    elif turn.kind in {"denied", "error"}:
        show_error(turn.message)
        if turn.prepared is not None and turn.kind == "error":
            show_prepared(turn.prepared)
    elif turn.kind == "result" and turn.result is not None:
        if turn.message:
            console.print(f"[brand]◆[/] [muted]{escape(turn.message)}[/]")
        mutating = turn.prepared is not None and turn.prepared.mutating
        show_result(turn.result, show_output=not mutating)
    for metric in turn.metrics:
        console.print(
            f"[muted]  ⚡ {metric.wall_sec:.1f}s · {metric.output_tokens} tokens · [/]"
            f"[ok]{metric.tokens_per_sec:.0f} tok/s[/]"
        )


HELP_COMMANDS = (
    ("/help", "esta ajuda"),
    ("/status", "estado do repositório"),
    ("/llm", "estado do modelo local (RAM/VRAM)"),
    ("/reset", "limpar a memória da conversa"),
    ("/clear", "limpar a tela"),
    ("exit", "sair (também /exit, sair, Ctrl-D ou Ctrl-C duas vezes)"),
)
HELP_EXAMPLES = (
    "mostre o que foi alterado",
    "faça commit do formulário",
    "crie a branch fix/login",
    "desfaça o último commit mantendo as alterações",
    "atualize minha branch com develop",
    "envie minhas alterações para o remoto",
    "faça cherry-pick do commit abc1234",
)


def show_help() -> None:
    table = Table.grid(padding=(0, 3))
    table.add_column(style="brand", no_wrap=True)
    table.add_column()
    for command, text in HELP_COMMANDS:
        table.add_row(command, text)
    examples = "\n".join(f"  [accent]›[/] {escape(e)}" for e in HELP_EXAMPLES)
    console.print(
        Panel(
            Group(
                table,
                Text(""),
                Text.from_markup("[label]Exemplos:[/]\n" + examples),
                Text(""),
                Text.from_markup(
                    "[muted]Operações que alteram o repositório sempre mostram o "
                    "impacto e pedem confirmação.[/]"
                ),
            ),
            title="[brand] ajuda [/]",
            title_align="left",
            border_style="border.brand",
            padding=(0, 1),
        )
    )
