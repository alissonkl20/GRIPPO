from app.harness.skills.registry import skill_registry


def test_mvp_skills_loaded():
    ids = {s.id for s in skill_registry.all()}
    expected = {
        "inspect-worktree",
        "stage-changes",
        "semantic-commit",
        "push",
        "stash-save",
        "stash-restore",
        "branch-create",
        "branch-switch",
    }
    assert expected <= ids
