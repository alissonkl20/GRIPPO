"""Interactive REPL and one-shot mode."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from grippo.agent.assistant import TurnResult
from grippo.cli import renderer
from grippo.cli.confirm import confirm_and_run
from grippo.cli.theme import ansi, strip_ansi
from grippo.engine.permissions import AUTONOMY_LEVELS
from grippo.errors import GrippoError
from grippo.runtime import Runtime


def make_reader(history_file: Path | None) -> Callable[[str], str]:
    if sys.stdin.isatty() and sys.stdout.isatty():
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory, InMemoryHistory

        history = InMemoryHistory()
        if history_file is not None:
            try:
                history_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                history = FileHistory(str(history_file))  # type: ignore[assignment]
            except OSError:
                pass
        from prompt_toolkit.formatted_text import ANSI

        session: PromptSession[str] = PromptSession(history=history)
        return lambda prompt: session.prompt(ANSI(prompt))

    def plain(prompt: str) -> str:
        sys.stdout.write(strip_ansi(prompt))
        sys.stdout.flush()
        line = sys.stdin.readline()
        if not line:
            raise EOFError
        return line.rstrip("\n")

    return plain


EXIT_WORDS = frozenset({"exit", "quit", "sair", "q", ":q", "tchau", "bye"})


def is_exit(message: str) -> bool:
    text = message.strip().lower().rstrip(".!")
    return text.lstrip("/") in EXIT_WORDS


def llm_label(runtime: Runtime) -> str:
    if runtime.llm is None:
        return "desativada (roteador determinístico)"
    return f"{runtime.config.llm.model} via Ollama ({runtime.config.llm.base_url})"


def run_turn(runtime: Runtime, message: str, read: Callable[[str], str]) -> TurnResult:
    with renderer.console.status(
        "[brand]GRIPPO analisando…[/]", spinner="dots", spinner_style="brand"
    ):
        turn = runtime.assistant.handle(message)
    renderer.show_turn(turn)
    if turn.kind == "confirm":
        result = confirm_and_run(runtime.assistant, turn, read)
        if result is not None:
            renderer.show_result(result, show_output=False)
            if result.final_state is not None:
                renderer.show_state(result.final_state)
            turn.result = result
    return turn


def _llm_status(runtime: Runtime) -> None:
    if runtime.llm is None:
        renderer.show_info("LLM desativada.")
        return
    try:
        version = runtime.llm.version()
        loaded = runtime.llm.loaded_models()
    except GrippoError as error:
        renderer.show_error(str(error))
        return
    renderer.show_info(f"Ollama {version} · modelo configurado: {runtime.config.llm.model}")
    for model in loaded:
        size = model.get("size", 0) / 2**30
        vram = model.get("size_vram", 0) / 2**30
        renderer.show_info(
            f"  carregado: {model.get('name')}  total {size:.2f} GiB · "
            f"VRAM {vram:.2f} GiB · RAM {size - vram:.2f} GiB"
        )
    if not loaded:
        renderer.show_info("  nenhum modelo carregado no momento (carrega na primeira pergunta)")


def _prompt(runtime: Runtime) -> str:
    """`grippo develop ❯ ` — branch read cheaply on every prompt (~10 ms)."""
    try:
        state = runtime.executor.state()
        branch = state.branch or state.short_head
        dirty = "" if state.clean else ansi("*", "warn")
    except GrippoError:
        branch, dirty = "?", ""
    return f"{ansi('grippo', 'brand')} {ansi(branch, 'branch')}{dirty} {ansi('❯', 'brand')} "


def repl(runtime: Runtime) -> int:
    read = make_reader(runtime.paths.history_file)
    renderer.show_header(
        animate=runtime.config.ui.animation, cycles=runtime.config.ui.animation_cycles
    )
    try:
        state = runtime.executor.state()
    except GrippoError as error:
        renderer.show_error(str(error))
        state = None
    level = runtime.config.security.autonomy_level
    renderer.show_welcome(
        runtime.repo.root, state, llm_label(runtime), f"nível {level} · {AUTONOMY_LEVELS[level]}"
    )
    interrupts = 0
    while True:
        try:
            message = read(_prompt(runtime)).strip()
            interrupts = 0
        except KeyboardInterrupt:
            interrupts += 1
            if interrupts >= 2:
                renderer.show_info("até logo! 🐆", "brand.dim")
                return 0
            renderer.show_info("(Ctrl-C de novo, exit ou Ctrl-D para sair)", "muted")
            continue
        except EOFError:
            renderer.show_info("até logo! 🐆", "brand.dim")
            return 0
        if not message:
            continue
        if is_exit(message):
            renderer.show_info("até logo! 🐆", "brand.dim")
            return 0
        if message.startswith("/"):
            command = message.split()[0].lower()
            if command == "/help":
                renderer.show_help()
            elif command == "/status":
                renderer.show_state(runtime.executor.state())
            elif command == "/clear":
                renderer.console.clear()
            elif command == "/reset":
                runtime.assistant.session.clear()
                renderer.show_info("memória da conversa limpa.", "muted")
            elif command == "/llm":
                _llm_status(runtime)
            else:
                renderer.show_error(f"comando desconhecido: {command} (veja /help)")
            continue
        try:
            run_turn(runtime, message, read)
        except KeyboardInterrupt:
            renderer.show_info("interrompido.", "warn")
            renderer.show_state(runtime.executor.state())
