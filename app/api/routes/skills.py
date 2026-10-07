from fastapi import APIRouter, HTTPException

from app.harness.skills.registry import skill_registry

router = APIRouter(prefix="/skills")


@router.get("")
def list_skills() -> list[dict]:
    return [
        {
            "id": s.id,
            "version": s.version,
            "description": s.description,
            "phase": s.phase,
            "tools": s.tools,
        }
        for s in skill_registry.all()
    ]


@router.get("/{skill_id}")
def get_skill(skill_id: str) -> dict:
    skill = skill_registry.get(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Unknown skill: {skill_id}")
    return {
        "id": skill.id,
        "version": skill.version,
        "description": skill.description,
        "phase": skill.phase,
        "tools": skill.tools,
        "requires_clean_conflicts": skill.requires_clean_conflicts,
        "max_steps": skill.max_steps,
        "body": skill.body,
    }
