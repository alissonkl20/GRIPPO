"""Skill guides (SKILL.md with YAML frontmatter), shipped as package data.

Skills document *how* GRIPPO approaches a flow (they are read by humans and exposed via
the API); safety never depends on them — the operation catalog and the permission
manager enforce the rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass(frozen=True)
class SkillDefinition:
    id: str
    version: int
    description: str
    phase: tuple[str, ...]
    tools: tuple[str, ...]
    requires_clean_conflicts: bool
    max_steps: int | None
    body: str
    path: Path


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        raise ValueError("SKILL.md deve começar com frontmatter YAML (---)")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("frontmatter inválido em SKILL.md")
    meta = yaml.safe_load(parts[1]) or {}
    if not isinstance(meta, dict):
        raise ValueError("frontmatter deve ser um mapa YAML")
    return meta, parts[2].lstrip("\n")


def load_skill_file(path: Path) -> SkillDefinition:
    meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    return SkillDefinition(
        id=str(meta.get("id") or path.parent.name),
        version=int(meta.get("version", 1)),
        description=str(meta.get("description", "")),
        phase=tuple(meta.get("phase") or ()),
        tools=tuple(meta.get("tools") or ()),
        requires_clean_conflicts=bool(meta.get("requires_clean_conflicts", False)),
        max_steps=meta.get("max_steps"),
        body=body,
        path=path,
    )


def discover_skills(dirs: tuple[Path, ...]) -> list[SkillDefinition]:
    found: dict[str, SkillDefinition] = {}
    for base in dirs:
        if not base.is_dir():
            continue
        for skill_md in sorted(base.glob("*/SKILL.md")):
            skill = load_skill_file(skill_md)
            found.setdefault(skill.id, skill)
    return list(found.values())


@lru_cache(maxsize=1)
def builtin_skills() -> tuple[SkillDefinition, ...]:
    """Loaded lazily (not at import time) and cached."""
    return tuple(discover_skills((DATA_DIR,)))


def get_skill(skill_id: str) -> SkillDefinition | None:
    return next((s for s in builtin_skills() if s.id == skill_id), None)
