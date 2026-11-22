"""Minimal MCP server over stdio (newline-delimited JSON-RPC 2.0).

Hand-rolled on purpose: GRIPPO needs only ``initialize``, ``tools/list`` and ``tools/call``,
and avoiding the SDK keeps the install small and the attack surface auditable.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import IO, Any

from grippo import __version__
from grippo.mcp.tools import TOOL_DEFS, GrippoTools

log = logging.getLogger(__name__)

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602

INSTRUCTIONS = (
    "GRIPPO is the Git layer for this repository. Look with grippo_repo_state, propose "
    "changes with grippo_propose (always quote the user's words in user_request and never "
    "invent branches, commits or paths), and tell the user to run `grippo approve <id>` in "
    "their own terminal. You cannot approve or execute changes yourself."
)


class McpServer:
    def __init__(self, tools: GrippoTools) -> None:
        self.tools = tools

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Return the JSON-RPC response, or ``None`` for notifications."""
        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return _error(request_id, INVALID_REQUEST, "requisição inválida")
        if request_id is None:  # notification (e.g. notifications/initialized)
            return None
        if method == "initialize":
            wanted = params.get("protocolVersion")
            version = wanted if wanted in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
            client = (params.get("clientInfo") or {}).get("name", "")
            self.tools.client = str(client)[:60]
            return _result(
                request_id,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "grippo", "version": __version__},
                    "instructions": INSTRUCTIONS,
                },
            )
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": TOOL_DEFS})
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return _error(request_id, INVALID_PARAMS, "name/arguments inválidos")
            reply = self.tools.call(name, arguments)
            return _result(
                request_id,
                {"content": [{"type": "text", "text": reply.text}], "isError": reply.is_error},
            )
        return _error(request_id, METHOD_NOT_FOUND, f"método não suportado: {method}")

    def serve(self, stdin: IO[str] = sys.stdin, stdout: IO[str] = sys.stdout) -> None:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                response: dict[str, Any] | None = _error(None, PARSE_ERROR, "JSON inválido")
            else:
                if not isinstance(message, dict):
                    response = _error(None, INVALID_REQUEST, "esperado um objeto JSON")
                else:
                    try:
                        response = self.handle(message)
                    except Exception:
                        log.exception("falha ao tratar %s", message.get("method"))
                        response = _error(message.get("id"), -32603, "erro interno")
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
