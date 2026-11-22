"""`grippo` entry point."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from grippo import __version__
from grippo.errors import GrippoError

if TYPE_CHECKING:
    from grippo.runtime import Runtime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grippo",
        description="GRIPPO — assistente Git local em linguagem natural. Sem subcomando, "
        "abre o modo interativo no repositório do diretório atual.",
    )
    parser.add_argument("--version", action="version", version=f"grippo {__version__}")
    parser.add_argument("--repo", type=Path, help="repositório a operar (padrão: diretório atual)")
    parser.add_argument(
        "--no-llm", action="store_true", help="não usar o modelo local (roteador determinístico)"
    )
    sub = parser.add_subparsers(dest="command", metavar="COMANDO")

    ask = sub.add_parser("ask", help="executar um único pedido e sair")
    ask.add_argument("message", nargs="+", help="pedido em linguagem natural")
    ask.add_argument("--json", action="store_true", help="saída JSON (somente a proposta/estado)")

    sub.add_parser("status", help="estado do repositório")

    doctor = sub.add_parser("doctor", help="diagnosticar instalação, Git e LLM")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument(
        "--self-test",
        action="store_true",
        help="executar testes essenciais em repositório temporário",
    )
    doctor.add_argument("--offline", action="store_true", help="não contatar o Ollama")

    config = sub.add_parser("config", help="ver ou alterar configuração")
    config_sub = config.add_subparsers(dest="action")
    config_sub.add_parser("show", help="configuração efetiva")
    config_sub.add_parser("path", help="caminhos usados pelo GRIPPO")
    config_sub.add_parser("init", help="criar config.toml padrão (não sobrescreve)")
    setter = config_sub.add_parser("set", help="definir secao.chave valor")
    setter.add_argument("key")
    setter.add_argument("value")

    model = sub.add_parser("model", help="gerenciar o modelo local (Ollama)")
    model_sub = model.add_subparsers(dest="action")
    model_sub.add_parser("list", help="modelos instalados e carregados")
    pull = model_sub.add_parser("pull", help="baixar modelo")
    pull.add_argument("name", nargs="?")
    imp = model_sub.add_parser("import", help="registrar um arquivo GGUF local no Ollama")
    imp.add_argument("path", type=Path)
    imp.add_argument("--name", required=True)
    model_sub.add_parser("unload", help="descarregar o modelo da memória")

    bench = sub.add_parser("bench", help="medir desempenho e consumo de recursos")
    bench.add_argument("--runs", type=int, default=5)
    bench.add_argument("--json", action="store_true")

    update = sub.add_parser("update", help="atualizar o GRIPPO")
    update.add_argument("--version", dest="target", help="versão específica (ex.: 0.2.0)")
    update.add_argument("--check", action="store_true", help="apenas verificar")
    update.add_argument("--pre", action="store_true", help="considerar pré-releases")
    update.add_argument(
        "--yes", action="store_true", help="não perguntar (consentimento explícito)"
    )

    mcp = sub.add_parser("mcp", help="servidor MCP: usar o GRIPPO a partir de outras IAs")
    mcp.add_argument(
        "--print-config", action="store_true", help="mostrar a configuração do cliente"
    )

    approve = sub.add_parser("approve", help="aprovar uma mudança proposta por uma IA")
    approve.add_argument("id", nargs="?", help="id da proposta (padrão: a única pendente)")
    sub.add_parser("proposals", help="listar propostas feitas por IAs")
    sub.add_parser("backups", help="listar backups de segurança criados antes de operações")

    uninstall = sub.add_parser("uninstall", help="remover o GRIPPO")
    uninstall.add_argument(
        "--purge",
        action="store_true",
        help="remover também configurações, modelos, dados, logs e auditoria",
    )
    uninstall.add_argument("--yes", action="store_true")
    return parser


def _cmd_ask(args: argparse.Namespace) -> int:
    from grippo.cli import renderer
    from grippo.cli.interface import make_reader, run_turn
    from grippo.runtime import build_runtime

    runtime = build_runtime(args.repo, use_llm=not args.no_llm)
    try:
        message = " ".join(args.message)
        if args.json:
            turn = runtime.assistant.handle(message)
            payload: dict[str, object] = {
                "kind": turn.kind,
                "message": turn.message,
                "notes": turn.notes,
            }
            if turn.prepared is not None:
                payload["risk"] = turn.prepared.risk.label
                payload["needs_confirmation"] = turn.prepared.needs_confirmation
                payload["steps"] = [{"op": s.op, "args": s.args} for s in turn.prepared.plan.steps]
            if turn.result is not None:
                payload["status"] = turn.result.status.value
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        turn = run_turn(runtime, message, make_reader(None))
        if turn.kind in {"error", "denied"}:
            return 1
        if turn.result is not None and not turn.result.ok:
            return 1
        if turn.kind == "confirm" and turn.result is None:
            return 3  # cancelled
        renderer.console.print()
        return 0
    finally:
        runtime.close()


def _cmd_status(args: argparse.Namespace) -> int:
    from grippo.cli import renderer
    from grippo.runtime import build_runtime

    runtime = build_runtime(args.repo, use_llm=False)
    try:
        renderer.show_state(runtime.executor.state())
        return 0
    finally:
        runtime.close()


def _cmd_config(args: argparse.Namespace) -> int:
    from grippo.config import dump_toml, load_config, set_value, write_default_config
    from grippo.paths import get_paths

    paths = get_paths()
    action = args.action or "show"
    if action == "path":
        for label, value in (
            ("config", paths.config_file),
            ("dados", paths.data_dir),
            ("modelos", paths.models_dir),
            ("estado", paths.state_dir),
            ("logs", paths.logs_dir),
            ("auditoria", paths.audit_dir),
            ("executável", paths.launcher),
        ):
            print(f"{label:11} {value}")
        return 0
    if action == "init":
        created = write_default_config(paths.config_file)
        print(("criado: " if created else "já existe (não alterado): ") + str(paths.config_file))
        return 0
    if action == "set":
        set_value(paths.config_file, args.key, args.value)
        print(f"{args.key} atualizado em {paths.config_file}")
        return 0
    print(dump_toml(load_config(paths.config_file).to_dict()))
    return 0


@contextmanager
def _runtime(args: argparse.Namespace, *, use_llm: bool = False) -> Iterator[Runtime]:
    from grippo.runtime import build_runtime

    runtime = build_runtime(args.repo, use_llm=use_llm)
    try:
        yield runtime
    finally:
        runtime.close()


def _cmd_mcp(args: argparse.Namespace) -> int:
    if args.print_config:
        from grippo.mcp.setup import render_config

        print(render_config(args.repo))
        return 0
    from grippo.mcp.proposals import ProposalStore
    from grippo.mcp.server import McpServer
    from grippo.mcp.tools import GrippoTools

    with _runtime(args) as runtime:
        McpServer(GrippoTools(runtime, ProposalStore(runtime.paths.proposals_dir))).serve()
    return 0


def _cmd_backups(args: argparse.Namespace) -> int:
    from grippo.engine.backup import list_backups

    with _runtime(args) as runtime:
        rows = list_backups(runtime.repo.root)
    for ref, sha, subject in rows:
        print(f"{ref}  {sha}  {subject}")
    print(
        "\nrecuperar: git reset --hard <ref>   ou   git branch recuperada <ref>"
        if rows
        else "nenhum backup de segurança."
    )
    return 0


def _dispatch(args: argparse.Namespace) -> int:
    command = args.command
    if command is None:
        from grippo.cli.interface import repl
        from grippo.runtime import build_runtime

        runtime = build_runtime(args.repo, use_llm=not args.no_llm)
        try:
            return repl(runtime)
        finally:
            runtime.close()
    if command == "ask":
        return _cmd_ask(args)
    if command == "status":
        return _cmd_status(args)
    if command == "config":
        return _cmd_config(args)
    if command == "doctor":
        from grippo.manage.doctor import run_doctor

        return run_doctor(
            args.repo, as_json=args.json, self_test=args.self_test, offline=args.offline
        )
    if command == "model":
        from grippo.manage.models import run_model

        return run_model(args)
    if command == "bench":
        from grippo.manage.bench import run_bench

        return run_bench(args.repo, runs=args.runs, as_json=args.json, use_llm=not args.no_llm)
    if command == "update":
        from grippo.manage.update import run_update

        return run_update(
            target=args.target, check_only=args.check, include_pre=args.pre, assume_yes=args.yes
        )
    if command == "mcp":
        return _cmd_mcp(args)
    if command == "approve":
        from grippo.mcp.approval import run_approve

        return run_approve(args.id)
    if command == "proposals":
        from grippo.mcp.approval import run_proposals

        return run_proposals()
    if command == "backups":
        return _cmd_backups(args)
    if command == "uninstall":
        from grippo.manage.uninstall import run_uninstall

        return run_uninstall(purge=args.purge, assume_yes=args.yes)
    raise AssertionError(command)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _dispatch(args)
    except GrippoError as error:
        print(f"grippo: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrompido.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
