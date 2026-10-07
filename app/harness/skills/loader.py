from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SkillDefinition:
    id: str
    version: int
    description: str
    phase: list[str]
    tools: list[str]
    requires_clean_conflicts: bool
    max_steps: int | None
    body: str
    path: Path


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise ValueError("SKILL.md must start with YAML frontmatter (---)")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("Invalid frontmatter in SKILL.md")
    meta = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\n")
    return meta, body


def load_skill_file(path: Path) -> SkillDefinition:
    raw = path.read_text(encoding="utf-8")
    meta, body = _split_frontmatter(raw)
    skill_id = meta.get("id") or path.parent.name
    return SkillDefinition(
        id=skill_id,
        version=int(meta.get("version", 1)),
        description=str(meta.get("description", "")),
        phase=list(meta.get("phase") or []),
        tools=list(meta.get("tools") or []),
        requires_clean_conflicts=bool(meta.get("requires_clean_conflicts", False)),
        max_steps=meta.get("max_steps"),
        body=body,
        path=path,
    )


def discover_skills(dirs: list[Path]) -> list[SkillDefinition]:
    found: list[SkillDefinition] = []
    seen: set[str] = set()
    for base in dirs:
        if not base.is_dir():
            continue
        for skill_md in sorted(base.glob("*/SKILL.md")):
            skill = load_skill_file(skill_md)
            if skill.id in seen:
                continue
            seen.add(skill.id)
            found.append(skill)
    return found
