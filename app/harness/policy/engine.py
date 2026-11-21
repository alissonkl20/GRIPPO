from dataclasses import dataclass
from enum import Enum


class PolicyTier(str, Enum):
    SAFE = "safe"
    MUTABLE = "mutable"
    DANGEROUS = "dangerous"


TOOL_TIERS: dict[str, PolicyTier] = {
    "git_status": PolicyTier.SAFE,
    "git_diff": PolicyTier.SAFE,
    "git_diff_cached": PolicyTier.SAFE,
    "git_log": PolicyTier.SAFE,
    "git_stage": PolicyTier.MUTABLE,
    "git_commit": PolicyTier.MUTABLE,
    "git_push": PolicyTier.MUTABLE,
    "git_stash_push": PolicyTier.MUTABLE,
    "git_checkout": PolicyTier.MUTABLE,
}


@dataclass
class PolicyDecision:
    allowed: bool
    needs_approval: bool
    tier: PolicyTier
    reason: str | None = None


def tier_for_tool(name: str) -> PolicyTier:
    return TOOL_TIERS.get(name, PolicyTier.DANGEROUS)


def evaluate(tool: str, *, confirmed: bool, force: bool = False) -> PolicyDecision:
    if force and tool == "git_push":
        return PolicyDecision(False, True, PolicyTier.DANGEROUS, "force_push_denied")
    tier = tier_for_tool(tool)
    if tier == PolicyTier.DANGEROUS:
        return PolicyDecision(False, True, tier, "dangerous")
    if tool in {"git_commit", "git_push"} and not confirmed:
        return PolicyDecision(False, True, tier, "needs_confirmation")
    return PolicyDecision(True, False, tier)
