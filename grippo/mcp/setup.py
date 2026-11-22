"""Ready-to-paste configuration for AI tools that speak MCP."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from grippo.paths import get_paths


def _command() -> str:
    return shutil.which("grippo") or str(get_paths().launcher)


def server_entry(repo: Path | None) -> dict[str, object]:
    args = (["--repo", str(repo.resolve())] if repo else []) + ["mcp"]
    return {"command": _command(), "args": args}


def render_config(repo: Path | None = None) -> str:
    entry = server_entry(repo)
    quoted = " ".join([str(entry["command"]), *map(str, entry["args"])])  # type: ignore[call-overload]
    return "\n".join(
        [
            "# Claude Code (escopo do projeto):",
            f"claude mcp add grippo -- {quoted}",
            "",
            "# Claude Desktop / Cursor / Windsurf / qualquer cliente MCP (JSON):",
            json.dumps({"mcpServers": {"grippo": entry}}, indent=2),
            "",
            "# Sem --repo, o servidor opera no diretório em que o cliente foi aberto.",
            "# Aprovação das mudanças propostas pela IA (no SEU terminal):",
            "#   grippo approve            # a proposta pendente",
            "#   grippo proposals          # histórico",
        ]
    )
