"""Mocked transport: these tests prove error handling, NOT real model behaviour."""

import threading
import time

import httpx
import pytest

from grippo.errors import (
    LLMOutOfMemoryError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from tests.conftest import FakeOllama


def test_chat_json_and_metrics(fake_ollama: FakeOllama):
    fake_ollama.replies.append({"ok": 1})
    client = fake_ollama.client(num_gpu=0, num_thread=4)
    data, reply = client.chat_json([{"role": "user", "content": "x"}], {"type": "object"})
    assert data == {"ok": 1}
    assert reply.metrics.output_tokens == 20 and reply.metrics.tokens_per_sec == pytest.approx(100)
    sent = fake_ollama.calls[0]
    assert sent["options"]["num_gpu"] == 0 and sent["options"]["num_thread"] == 4
    assert sent["options"]["num_ctx"] == 4096 and sent["stream"] is False
    assert sent["format"] == {"type": "object"} and sent["keep_alive"] == "10m"


def test_gpu_auto_omits_num_gpu(fake_ollama: FakeOllama):
    fake_ollama.replies.append({"ok": 1})
    fake_ollama.client().chat_json([{"role": "user", "content": "x"}], {})
    assert "num_gpu" not in fake_ollama.calls[0]["options"]


def test_missing_model_is_an_error_not_a_silent_fallback():
    fake = FakeOllama(model="other")
    client = fake.client()
    client.config.model = "wanted:3b"
    with pytest.raises(LLMUnavailableError, match="wanted:3b"):
        client.chat([{"role": "user", "content": "x"}])


def test_connection_error_maps_to_unavailable():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    fake = FakeOllama()
    client = fake.client()
    client._transport = httpx.MockTransport(boom)
    with pytest.raises(LLMUnavailableError):
        client.version()


def test_timeout_maps(fake_ollama: FakeOllama):
    fake_ollama.replies.append(httpx.ReadTimeout("slow"))
    with pytest.raises(LLMTimeoutError):
        fake_ollama.client().chat([{"role": "user", "content": "x"}])


def test_out_of_memory_maps(fake_ollama: FakeOllama):
    fake_ollama.replies.append(httpx.Response(
        500, json={"error": "model requires more system memory (5.1 GiB) than is available"}))
    with pytest.raises(LLMOutOfMemoryError, match="num_ctx"):
        fake_ollama.client().chat([{"role": "user", "content": "x"}])


def test_invalid_json_maps(fake_ollama: FakeOllama):
    fake_ollama.replies.append("isto não é json")
    with pytest.raises(LLMResponseError):
        fake_ollama.client().chat_json([{"role": "user", "content": "x"}], {})


def test_single_inference_at_a_time(fake_ollama: FakeOllama):
    active = []
    peak = []
    original = fake_ollama.handler

    def slow(request):
        if request.url.path == "/api/chat":
            active.append(1)
            peak.append(len(active))
            time.sleep(0.05)
            active.pop()
        return original(request)

    client = fake_ollama.client()
    client._transport = httpx.MockTransport(slow)
    fake_ollama.replies.extend([{"n": i} for i in range(6)])
    threads = [threading.Thread(target=client.chat_json, args=([{"role": "user",
                                                                 "content": "x"}], {}))
               for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max(peak) == 1
    assert len(client.history) == 6


def test_client_reuses_connection_pool(fake_ollama: FakeOllama):
    client = fake_ollama.client()
    first = client.http
    client.version()
    assert client.http is first
    client.close()
    assert client._client is None
