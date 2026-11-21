from __future__ import annotations


def route_intent(user_message: str) -> str:
    text = user_message.lower()
    wants_commit = any(
        token in text for token in ("commit", "commita", "commitar", "commite")
    )
    wants_push = any(
        token in text
        for token in ("push", "empurra", "enviar", "envia", "manda pro", "manda para o remoto")
    )
    if "manda pro remoto" in text or "manda para o remoto" in text:
        wants_push = True
    if wants_commit and wants_push:
        return "commit_push"
    if wants_commit:
        return "commit"
    if wants_push or "manda pro" in text:
        return "push"
    return "inspect"
