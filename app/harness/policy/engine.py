from enum import Enum


class PolicyTier(str, Enum):
    SAFE = "safe"
    MUTABLE = "mutable"
    DANGEROUS = "dangerous"


TOOL_TIERS: dict[str, PolicyTier] = {
    "git_status": PolicyTier.SAFE,
    "git_diff": PolicyTier.SAFE,
    "git_stage": PolicyTier.MUTABLE,
    "git_commit": PolicyTier.MUTABLE,
    "git_push": PolicyTier.MUTABLE,
    "git_stash_push": PolicyTier.MUTABLE,
    "git_checkout": PolicyTier.MUTABLE,
}


def tier_for_tool(name: str) -> PolicyTier:
    return TOOL_TIERS.get(name, PolicyTier.DANGEROUS)
