"""Interactive REPL and one-shot mode."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from grippo.agent.assistant import TurnResult
from grippo.cli import renderer
from grippo.cli.confirm import confirm_and_run
from grippo.engine.permissions import AUTONOMY_LEVELS
from grippo.errors import GrippoError
from grippo.runtime import Runtime

HELP = """\
[bold]Comandos[/bold]
  /help        esta ajuda
  /status      estado do repositório
  /llm         estado do modelo local
  /reset       limpar a memória da conversa
  /clear       limpar a tela
  /exit        sair (ou Ctrl-D)

Fale em linguagem natural, por exemplo:
  mostre o que foi alterado · faça commit do formulário · crie a branch fix/login
  desfaça o último commit mantendo as alterações · atualize minha branch com develop
  envie minhas alterações para o remoto · faça cherry-pick do commit abc1234

[dim]Operações que alteram o repositório sempre mostram o impacto e pedem confirmação.[/dim]
"""


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
        session: PromptSession[str] = PromptSession(history=history)
        return lambda prompt: session.prompt(prompt)

    def plain(prompt: str) -> str:
        sys.stdout.write(prompt)
        sys.stdout.flush()
        line = sys.stdin.readline()
        if not line:
            raise EOFError
        return line.rstrip("\n")

    return plain


def llm_label(runtime: Runtime) -> str:
    if runtime.llm is None:
        return "desativada (roteador determinístico)"
    return f"{runtime.config.llm.model} via Ollama ({runtime.config.llm.base_url})"


def run_turn(runtime: Runtime, message: str, read: Callable[[str], str]) -> TurnResult:
    with renderer.console.status("[bold green]GRIPPO analisando…[/bold green]", spinner="dots"):
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


def repl(runtime: Runtime) -> int:
    read = make_reader(runtime.paths.history_file)
    renderer.show_header()
    try:
        state = runtime.executor.state()
    except GrippoError as error:
        renderer.show_error(str(error))
        state = None
    renderer.show_welcome(runtime.repo.root, state, llm_label(runtime))
    level = runtime.config.security.autonomy_level
    renderer.show_info(f"autonomia nível {level}: {AUTONOMY_LEVELS[level]}", "dim")
    while True:
        try:
            message = read("❯ ").strip()
        except KeyboardInterrupt:
            continue
        except EOFError:
            renderer.show_info("até logo.", "dim")
            return 0
        if not message:
            continue
        if message.startswith("/"):
            command = message.split()[0].lower()
            if command in {"/exit", "/quit", "/sair"}:
                return 0
            if command == "/help":
                renderer.console.print(HELP)
            elif command == "/status":
                renderer.show_state(runtime.executor.state())
            elif command == "/clear":
                renderer.console.clear()
            elif command == "/reset":
                runtime.assistant.session.clear()
                renderer.show_info("memória da conversa limpa.", "dim")
            elif command == "/llm":
                _llm_status(runtime)
            else:
                renderer.show_error(f"comando desconhecido: {command} (veja /help)")
            continue
        try:
            run_turn(runtime, message, read)
        except KeyboardInterrupt:
            renderer.show_info("interrompido.", "yellow")
            renderer.show_state(runtime.executor.state())
