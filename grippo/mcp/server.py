"""Minimal MCP server over stdio (newline-delimited JSON-RPC 2.0).

Hand-rolled on purpose: GRIPPO needs only ``initialize``, ``tools/list`` and ``tools/call``,
and avoiding the SDK keeps the install small and the attack surface auditable.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from typing import IO, Any

from grippo import __version__
from grippo.mcp.tools import TOOL_DEFS, GrippoTools

log = logging.getLogger(__name__)

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = (
    -32700,
    -32600,
    -32601,
    -32602,
    -32603,
)

INSTRUCTIONS = (
    "GRIPPO is the Git layer for this repository. Look with grippo_repo_state, propose "
    "changes with grippo_propose (always quote the user's words in user_request and never "
    "invent branches, commits or paths), and tell the user to run `grippo approve <id>` in "
    "their own terminal. You cannot approve or execute changes yourself."
)

Params = dict[str, Any]
Response = dict[str, Any]


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class McpServer:
    def __init__(self, tools: GrippoTools) -> None:
        self.tools = tools
        self._methods: dict[str, Callable[[Params], Params]] = {
            "initialize": self._initialize,
            "ping": lambda _: {},
            "tools/list": lambda _: {"tools": TOOL_DEFS},
            "tools/call": self._call_tool,
        }

    def _initialize(self, params: Params) -> Params:
        wanted = params.get("protocolVersion")
        self.tools.client = str((params.get("clientInfo") or {}).get("name", ""))[:60]
        return {
            "protocolVersion": wanted if wanted in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "grippo", "version": __version__},
            "instructions": INSTRUCTIONS,
        }

    def _call_tool(self, params: Params) -> Params:
        name, arguments = params.get("name"), params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise RpcError(INVALID_PARAMS, "name/arguments inválidos")
        reply = self.tools.call(name, arguments)
        return {"content": [{"type": "text", "text": reply.text}], "isError": reply.is_error}

    def handle(self, message: Params) -> Response | None:
        """Return the JSON-RPC response, or ``None`` for notifications."""
        request_id, method = message.get("id"), message.get("method")
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return _error(request_id, INVALID_REQUEST, "requisição inválida")
        if request_id is None:  # notification (e.g. notifications/initialized)
            return None
        handler = self._methods.get(method)
        if handler is None:
            return _error(request_id, METHOD_NOT_FOUND, f"método não suportado: {method}")
        try:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": handler(message.get("params") or {}),
            }
        except RpcError as error:
            return _error(request_id, error.code, str(error))

    def _process_line(self, line: str) -> Response | None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return _error(None, PARSE_ERROR, "JSON inválido")
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "esperado um objeto JSON")
        try:
            return self.handle(message)
        except Exception:
            log.exception("falha ao tratar %s", message.get("method"))
            return _error(message.get("id"), INTERNAL_ERROR, "erro interno")

    def serve(self, stdin: IO[str] = sys.stdin, stdout: IO[str] = sys.stdout) -> None:
        for line in filter(None, (raw.strip() for raw in stdin)):
            if (response := self._process_line(line)) is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()


def _error(request_id: Any, code: int, message: str) -> Response:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
