"""Ready-to-paste configuration for AI tools that speak MCP."""

from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path

from grippo.paths import get_paths


def server_command(repo: Path | None) -> list[str]:
    """argv that starts the server (``--repo`` is a global option, so it precedes ``mcp``)."""
    command = shutil.which("grippo") or str(get_paths().launcher)
    return [command, *(["--repo", str(repo.resolve())] if repo else []), "mcp"]


def render_config(repo: Path | None = None) -> str:
    command, *args = server_command(repo)
    entry = {"command": command, "args": args}
    return "\n".join(
        [
            "# Claude Code (escopo do projeto):",
            f"claude mcp add grippo -- {shlex.join([command, *args])}",
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
