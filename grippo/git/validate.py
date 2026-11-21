"""Argument validators. Everything that reaches git argv passes through here first."""

from __future__ import annotations

import os.path
import re
from pathlib import Path

from grippo.errors import ValidationError

_REV_CHARS = re.compile(r"^[A-Za-z0-9._/@{}~^+\-]+$")
_REF_BAD_CHARS = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")
_REMOTE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_SCP_URL = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[^\s]+$")
_ALLOWED_SCHEMES = ("https://", "http://", "ssh://", "git://", "file://")

SENSITIVE_NAMES = frozenset(
    {
        ".env",
        "credentials.json",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        ".npmrc",
        ".pypirc",
        ".netrc",
        "secrets.yaml",
        "secrets.yml",
    }
)
SENSITIVE_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"})


def validate_ref_name(name: object, what: str = "nome de referência") -> str:
    """Name for a NEW branch/tag (git check-ref-format rules, plus no leading '-')."""
    if not isinstance(name, str) or not name.strip():
        raise ValidationError(f"{what} vazio")
    name = name.strip()
    problems = (
        len(name) > 200,
        name.startswith(("-", "/", ".")),
        name.endswith(("/", ".", ".lock")),
        ".." in name,
        "@{" in name,
        "//" in name,
        name == "@",
        bool(_REF_BAD_CHARS.search(name)),
        any(part.startswith(".") or part.endswith(".lock") for part in name.split("/")),
    )
    if any(problems):
        raise ValidationError(f"{what} inválido: {name!r}")
    return name


def validate_revision(rev: object, *, allow_range: bool = False) -> str:
    """An existing revision expression such as ``HEAD~1``, ``origin/main`` or a SHA."""
    if not isinstance(rev, str) or not rev.strip():
        raise ValidationError("revisão vazia")
    rev = rev.strip()
    if len(rev) > 200 or rev.startswith("-") or not _REV_CHARS.match(rev):
        raise ValidationError(f"revisão inválida: {rev!r}")
    if ".." in rev and not allow_range:
        raise ValidationError(f"intervalos não são aceitos aqui: {rev!r}")
    return rev


def validate_remote_name(name: object) -> str:
    if not isinstance(name, str) or not _REMOTE_NAME.match(name.strip()):
        raise ValidationError(f"nome de remoto inválido: {name!r}")
    return name.strip()


def validate_url(url: object) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValidationError("URL vazia")
    url = url.strip()
    if url.startswith("-") or "::" in url or any(c in url for c in "\x00\n\r\t "):
        raise ValidationError(f"URL não permitida: {url!r}")
    if url.startswith(_ALLOWED_SCHEMES) or _SCP_URL.match(url) or url.startswith("/"):
        return url
    raise ValidationError(f"URL não permitida: {url!r}")


def validate_message(message: object, *, max_len: int = 5000) -> str:
    if not isinstance(message, str) or not message.strip():
        raise ValidationError("mensagem vazia")
    if "\x00" in message or len(message) > max_len:
        raise ValidationError("mensagem inválida")
    return message.strip()


def validate_int(value: object, *, low: int, high: int, what: str) -> int:
    try:
        number = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError) as error:
        raise ValidationError(f"{what} deve ser inteiro") from error
    if isinstance(value, bool) or not low <= number <= high:
        raise ValidationError(f"{what} deve estar entre {low} e {high}")
    return number


def validate_path(repo_root: Path, raw: object) -> str:
    """Return a repo-relative path guaranteed to stay inside the work tree and outside .git."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValidationError("caminho vazio")
    if "\x00" in raw:
        raise ValidationError("caminho contém byte NUL")
    root = repo_root.resolve()
    candidate = Path(raw.strip())
    absolute = candidate if candidate.is_absolute() else root / candidate
    # Lexical normalization: a symlink inside the repo is a valid path to stage.
    resolved = Path(os.path.normpath(str(absolute)))
    try:
        relative = resolved.relative_to(root)
    except ValueError as error:
        raise ValidationError(f"caminho fora do repositório: {raw!r}") from error
    if ".git" in relative.parts:
        raise ValidationError(f"caminho dentro de .git: {raw!r}")
    text = relative.as_posix()
    if text in ("", "."):
        return "."
    return f"./{text}" if text.startswith("-") else text


def is_sensitive_path(path: str) -> bool:
    name = Path(path).name
    return (
        name in SENSITIVE_NAMES
        or Path(path).suffix in SENSITIVE_SUFFIXES
        or (name.startswith(".env.") and name != ".env.example")
    )
