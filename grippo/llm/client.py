"""Ollama client.

Why Ollama (and not llama-cpp-python inside the GRIPPO process): it was already the
backend of the project; it runs GGUF quantized models on CPU and/or GPU with partial
layer offload (``num_gpu``); a single server-side instance is shared by every
``grippo`` process (no duplicate weights in RAM/VRAM, no reload per launch);
``keep_alive`` controls residency; and it adds no compiled dependency to GRIPPO.

One persistent HTTP connection pool per client, one in-flight inference at a time
(small GPUs/CPUs do not benefit from concurrent generations), explicit timeouts,
structured (JSON-schema) output, and errors mapped to GRIPPO exceptions.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from grippo.config import LLMConfig
from grippo.errors import (
    LLMError,
    LLMOutOfMemoryError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMMetrics:
    wall_sec: float
    load_sec: float
    prompt_tokens: int
    prompt_sec: float
    output_tokens: int
    output_sec: float

    @property
    def tokens_per_sec(self) -> float:
        return self.output_tokens / self.output_sec if self.output_sec > 0 else 0.0

    @property
    def prompt_tokens_per_sec(self) -> float:
        return self.prompt_tokens / self.prompt_sec if self.prompt_sec > 0 else 0.0


@dataclass(frozen=True)
class LLMReply:
    text: str
    model: str
    metrics: LLMMetrics


class OllamaClient:
    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self._transport = transport
        self._client: httpx.Client | None = None
        self._client_lock = threading.Lock()
        self._inference = threading.BoundedSemaphore(1)
        self._model_ok_until = 0.0
        self.history: deque[LLMMetrics] = deque(maxlen=200)

    # ------------------------------------------------------------------ plumbing
    @property
    def http(self) -> httpx.Client:
        with self._client_lock:
            if self._client is None:
                self._client = httpx.Client(
                    base_url=self.config.base_url.rstrip("/"),
                    timeout=httpx.Timeout(
                        self.config.timeout_sec, connect=self.config.connect_timeout_sec
                    ),
                    transport=self._transport,
                )
            return self._client

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.ConnectError as error:
            raise LLMUnavailableError(
                f"Ollama indisponível em {self.config.base_url} (o serviço está rodando?)"
            ) from error
        except httpx.TimeoutException as error:
            raise LLMTimeoutError(
                f"Ollama não respondeu em {self.config.timeout_sec:.0f}s"
            ) from error
        except httpx.HTTPError as error:
            raise LLMError(f"erro de comunicação com Ollama: {error}") from error
        if response.status_code >= 400:
            self._raise_for(response)
        return response

    def _raise_for(self, response: httpx.Response) -> None:
        try:
            detail = str(response.json().get("error", response.text))
        except (json.JSONDecodeError, AttributeError):
            detail = response.text
        lowered = detail.lower()
        if "memory" in lowered or "cuda error" in lowered or "out of vram" in lowered:
            raise LLMOutOfMemoryError(
                f"memória insuficiente para o modelo: {detail}. Reduza llm.num_ctx, use "
                "llm.num_gpu menor (offload parcial) ou um modelo menor/quantização mais baixa."
            )
        if response.status_code == 404:
            raise LLMUnavailableError(
                f"modelo '{self.config.model}' não encontrado no Ollama "
                f"(rode: grippo model pull {self.config.model})"
            )
        raise LLMError(f"Ollama retornou {response.status_code}: {detail}")

    # ------------------------------------------------------------------ info
    def version(self) -> str:
        return str(self._request("GET", "/api/version").json().get("version", "?"))

    def list_models(self) -> list[dict[str, Any]]:
        models = self._request("GET", "/api/tags").json().get("models", [])
        return [m for m in models if isinstance(m, dict)]

    def loaded_models(self) -> list[dict[str, Any]]:
        models = self._request("GET", "/api/ps").json().get("models", [])
        return [m for m in models if isinstance(m, dict)]

    def ensure_model(self) -> None:
        """Fail clearly instead of silently using some other model. Cached for 60 s."""
        if time.monotonic() < self._model_ok_until:
            return
        models = self.list_models()
        names = {m.get("name") for m in models} | {m.get("model") for m in models}
        wanted = self.config.model
        if wanted not in names and f"{wanted}:latest" not in names:
            raise LLMUnavailableError(
                f"modelo '{wanted}' não está instalado no Ollama (rode: grippo model pull {wanted})"
            )
        self._model_ok_until = time.monotonic() + 60

    # ------------------------------------------------------------------ inference
    def _options(self, max_tokens: int) -> dict[str, Any]:
        options: dict[str, Any] = {
            "temperature": self.config.temperature,
            "num_ctx": self.config.num_ctx,
            "num_predict": max_tokens,
        }
        if self.config.num_gpu >= 0:
            options["num_gpu"] = self.config.num_gpu
        if self.config.num_thread > 0:
            options["num_thread"] = self.config.num_thread
        return options

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 256,
    ) -> LLMReply:
        self.ensure_model()
        if not self._inference.acquire(timeout=self.config.timeout_sec):
            raise LLMTimeoutError("outra inferência ainda está em andamento")
        try:
            started = time.monotonic()
            payload: dict[str, Any] = {
                "model": self.config.model,
                "messages": messages,
                "stream": False,
                "keep_alive": self.config.keep_alive,
                "options": self._options(max_tokens),
            }
            if schema is not None:
                payload["format"] = schema
            data = self._request("POST", "/api/chat", json=payload).json()
        finally:
            self._inference.release()
        content = (data.get("message") or {}).get("content")
        if not isinstance(content, str):
            raise LLMResponseError("resposta do modelo sem conteúdo")

        def seconds(key: str) -> float:
            return float(data.get(key) or 0) / 1e9

        metrics = LLMMetrics(
            wall_sec=time.monotonic() - started,
            load_sec=seconds("load_duration"),
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            prompt_sec=seconds("prompt_eval_duration"),
            output_tokens=int(data.get("eval_count") or 0),
            output_sec=seconds("eval_duration"),
        )
        self.history.append(metrics)
        log.info(
            "llm %s: %.2fs (load %.2fs) prompt=%d out=%d %.1f tok/s",
            self.config.model,
            metrics.wall_sec,
            metrics.load_sec,
            metrics.prompt_tokens,
            metrics.output_tokens,
            metrics.tokens_per_sec,
        )
        return LLMReply(content, str(data.get("model", self.config.model)), metrics)

    def chat_json(
        self, messages: list[dict[str, str]], schema: dict[str, Any], max_tokens: int = 256
    ) -> tuple[dict[str, Any], LLMReply]:
        reply = self.chat(messages, schema=schema, max_tokens=max_tokens)
        try:
            parsed = json.loads(reply.text)
        except json.JSONDecodeError as error:
            raise LLMResponseError(
                f"o modelo não retornou JSON válido: {reply.text[:120]!r}"
            ) from error
        if not isinstance(parsed, dict):
            raise LLMResponseError("o modelo retornou JSON que não é um objeto")
        return parsed, reply

    # ------------------------------------------------------------------ lifecycle
    def warm_up(self) -> None:
        """Load the model into memory without generating (keeps first answer fast)."""
        self.ensure_model()
        self._request(
            "POST",
            "/api/generate",
            json={
                "model": self.config.model,
                "keep_alive": self.config.keep_alive,
                "options": self._options(1),
            },
        )

    def unload(self) -> None:
        self._request("POST", "/api/generate", json={"model": self.config.model, "keep_alive": 0})

    def pull(self, name: str, on_progress: Callable[[str], None] | None = None) -> None:
        with self.http.stream(
            "POST", "/api/pull", json={"model": name, "stream": True}, timeout=None
        ) as response:
            if response.status_code >= 400:
                response.read()
                self._raise_for(response)
            for line in _lines(response.iter_lines()):
                event = json.loads(line)
                if "error" in event:
                    raise LLMError(str(event["error"]))
                if on_progress:
                    on_progress(str(event.get("status", "")))


def _lines(chunks: Iterator[str]) -> Iterator[str]:
    for chunk in chunks:
        if chunk.strip():
            yield chunk
