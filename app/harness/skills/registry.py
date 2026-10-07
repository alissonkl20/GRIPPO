from __future__ import annotations

from app.config import settings
from app.harness.skills.loader import SkillDefinition, discover_skills


class SkillRegistry:
    def __init__(self) -> None:
        self._by_id: dict[str, SkillDefinition] = {}
        self.reload()

    def reload(self) -> None:
        skills = discover_skills(settings.resolve_skills_dirs())
        self._by_id = {s.id: s for s in skills}

    def get(self, skill_id: str) -> SkillDefinition | None:
        return self._by_id.get(skill_id)

    def all(self) -> list[SkillDefinition]:
        return list(self._by_id.values())


skill_registry = SkillRegistry()
