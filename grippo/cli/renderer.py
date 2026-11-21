"""Terminal rendering (rich). No business logic here."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console, Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from grippo import __version__
from grippo.agent.assistant import TurnResult
from grippo.engine.operations import Risk
from grippo.engine.plan import PlanResult, PreparedPlan, StepStatus
from grippo.git.state import RepoState

console = Console(highlight=False)

_RISK_STYLE = {Risk.LOW: "green", Risk.MODERATE: "yellow", Risk.HIGH: "bold red"}
_STATUS_ICON = {
    StepStatus.DONE: "[green]✔[/green]",
    StepStatus.FAILED: "[red]✘[/red]",
    StepStatus.CONFLICT: "[yellow]⚠[/yellow]",
    StepStatus.INTERRUPTED: "[yellow]■[/yellow]",
    StepStatus.TIMEOUT: "[red]⏱[/red]",
    StepStatus.SKIPPED: "[dim]–[/dim]",
    StepStatus.REJECTED: "[red]⊘[/red]",
}
_MAX_OUTPUT_LINES = 80


def show_header(*, animate: bool = True, cycles: int = 2) -> None:
    """Cheetah header (animated on a real terminal; see grippo/cli/splash.py)."""
    from grippo.cli import splash

    splash.show(console.file, __version__, animate=animate, cycles=cycles)


def show_welcome(repo_root: Path, state: RepoState | None, llm_label: str) -> None:
    lines = [f"[dim]Repositório[/dim]  {escape(str(repo_root))}"]
    if state is not None:
        lines.append(
            f"[dim]Branch[/dim]       {escape(state.branch or '(HEAD destacado)')}"
            f"  [dim]HEAD[/dim] {state.short_head}"
        )
    lines.append(f"[dim]LLM[/dim]          {escape(llm_label)}")
    lines.append(
        "[dim]Digite /help para comandos. Para sair: exit, Ctrl-D ou Ctrl-C duas vezes.[/dim]"
    )
    console.print(Panel("\n".join(lines), border_style="dim", padding=(0, 1)))


def show_error(message: str) -> None:
    console.print(f"[bold red]Erro:[/bold red] {escape(message)}")


def show_info(message: str, style: str = "") -> None:
    console.print(f"[{style}]{escape(message)}[/{style}]" if style else escape(message))


def show_notes(notes: list[str]) -> None:
    for note in notes:
        console.print(f"[dim]· {escape(note)}[/dim]")


def show_state(state: RepoState) -> None:
    console.print(
        Panel(escape(state.summary()), title="estado", border_style="dim", padding=(0, 1))
    )


def _bullet(items: list[str], style: str = "") -> str:
    return "\n".join(
        f"  • [{style}]{escape(i)}[/{style}]" if style else f"  • {escape(i)}" for i in items
    )


def show_prepared(
    prepared: PreparedPlan, *, details: bool = False, warnings: list[str] | None = None
) -> None:
    state = prepared.state
    style = _RISK_STYLE[prepared.risk]
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Repositório", escape(prepared.repo_root))
    table.add_row(
        "Branch", escape(f"{state.branch or '(HEAD destacado)'}  HEAD {state.short_head}")
    )
    tree = (
        "limpo"
        if state.clean
        else (
            f"{len(state.staged)} staged, {len(state.unstaged)} "
            f"não staged, {len(state.untracked)} não rastreados"
        )
    )
    table.add_row(
        "Working tree", tree + (f" · {state.in_progress} em andamento" if state.in_progress else "")
    )
    table.add_row("Risco", f"[{style}]{prepared.risk.label.upper()}[/{style}]")
    body: list[RenderableType] = [table]
    for index, preview in enumerate(prepared.previews, start=1):
        lines = [
            f"[bold]{index}. {escape(preview.step.op)}[/bold]  "
            f"[{_RISK_STYLE[preview.risk]}]({preview.risk.label})[/]"
        ]
        args = {k: v for k, v in preview.step.args.items() if v not in (False, None, [], "")}
        if args:
            lines.append(f"   [dim]argumentos:[/dim] {escape(str(args))}")
        if details or prepared.risk is Risk.HIGH:
            lines.append(f"   [dim]comando:[/dim] git {escape(' '.join(preview.argv))}")
        impact = preview.impact
        if impact.consequences:
            lines.append(_bullet(impact.consequences))
        if impact.files:
            shown = impact.files if details else impact.files[:12]
            lines.append(
                "   [dim]arquivos:[/dim]\n"
                + _bullet(shown, "cyan")
                + (
                    f"\n  … +{len(impact.files) - len(shown)}"
                    if len(impact.files) > len(shown)
                    else ""
                )
            )
        if impact.commits:
            lines.append("   [dim]commits:[/dim]\n" + _bullet(impact.commits, "magenta"))
        for warning in impact.warnings:
            lines.append(f"   [yellow]⚠ {escape(warning)}[/yellow]")
        if impact.recovery:
            lines.append(f"   [dim]recuperação:[/dim] {escape(impact.recovery)}")
        for error in preview.errors:
            lines.append(f"   [red]✘ {escape(error)}[/red]")
        body.append(Text.from_markup("\n".join(lines)))
    for warning in (warnings or []) + list(prepared.plan.warnings):
        body.append(Text.from_markup(f"[yellow]⚠ {escape(warning)}[/yellow]"))
    console.print(
        Panel(Group(*body), title="operação proposta", border_style=style, padding=(0, 1))
    )


def _trim(text: str) -> str:
    lines = text.rstrip().splitlines()
    if len(lines) > _MAX_OUTPUT_LINES:
        omitted = len(lines) - _MAX_OUTPUT_LINES
        lines = [*lines[:_MAX_OUTPUT_LINES], f"… ({omitted} linhas omitidas)"]
    return "\n".join(lines)


def show_result(result: PlanResult, *, show_output: bool) -> None:
    for item in result.results:
        icon = _STATUS_ICON[item.status]
        line = f"{icon} [bold]{escape(item.step.op)}[/bold]"
        if item.command:
            line += f"  [dim]{escape(item.command)}[/dim]"
        console.print(line)
        if item.verification and item.status is not StepStatus.SKIPPED:
            style = "green" if item.verification.ok else "red"
            console.print(f"   [{style}]{escape(item.verification.message)}[/{style}]")
        if show_output and item.stdout.strip():
            console.print(escape(_trim(item.stdout)))
        if item.status is not StepStatus.DONE and item.stderr.strip():
            console.print(f"[dim]{escape(_trim(item.stderr))}[/dim]")
        if item.hint:
            console.print(f"   [yellow]→ {escape(item.hint)}[/yellow]")
    style = (
        "green"
        if result.ok
        else ("yellow" if result.status in (StepStatus.CONFLICT, StepStatus.INTERRUPTED) else "red")
    )
    console.print(f"[{style}]{escape(result.message)}[/{style}]")


def show_turn(turn: TurnResult) -> None:
    show_notes(turn.notes)
    if turn.kind in {"answer", "noop"}:
        console.print(escape(turn.message))
    elif turn.kind == "clarify":
        console.print(f"[cyan]?[/cyan] {escape(turn.message)}")
    elif turn.kind in {"denied", "error"}:
        show_error(turn.message)
        if turn.prepared is not None and turn.kind == "error":
            show_prepared(turn.prepared)
    elif turn.kind == "result" and turn.result is not None:
        if turn.message:
            console.print(f"[dim]{escape(turn.message)}[/dim]")
        mutating = turn.prepared is not None and turn.prepared.mutating
        show_result(turn.result, show_output=not mutating)
    for metric in turn.metrics:
        console.print(
            f"[dim]llm: {metric.wall_sec:.1f}s · {metric.output_tokens} tokens · "
            f"{metric.tokens_per_sec:.0f} tok/s[/dim]"
        )
