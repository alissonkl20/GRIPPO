"""Small text helpers shared by the router, the guard and the commit drafter."""

from __future__ import annotations

import unicodedata


def fold(text: str) -> str:
    """Lowercase without accents (so 'alterações' matches 'alteracoes')."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))
