from __future__ import annotations

import httpx

from app.config import settings


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().strip("`").strip()
        if stripped:
            return stripped[:200]
    return ""


def generate_commit_message(diff: str, files: list[str]) -> str | None:
    prompt = (
        "Write one Conventional Commit subject line only (type(scope): summary). "
        "Types: feat, fix, docs, chore, refactor, test. No quotes, no explanation.\n"
        f"Files: {', '.join(files[:40])}\n"
        f"Diff:\n{diff[: settings.max_diff_bytes]}\n"
    )
    try:
        with httpx.Client(timeout=8.0) as client:
            tags = client.get(f"{settings.ollama_base_url}/api/tags")
            tags.raise_for_status()
            names = [m.get("name") for m in tags.json().get("models", [])]
            if not names:
                return None
            model = settings.model if settings.model in names else names[0]
            response = client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.2, "num_predict": 40},
                },
            )
            response.raise_for_status()
    except httpx.HTTPError:
        return None
    return _first_line(response.json().get("response") or "") or None
